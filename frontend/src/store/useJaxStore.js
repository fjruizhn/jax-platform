import { create } from 'zustand'
import api from '../api/client'

function _loadPendingIds() {
  try { return JSON.parse(localStorage.getItem('jax_pending_cmds') || '[]') } catch { return [] }
}
function _savePendingIds(ids) {
  localStorage.setItem('jax_pending_cmds', JSON.stringify(ids))
}

function _stepsEqual(a, b) {
  const keys = Object.keys(a)
  if (keys.length !== Object.keys(b).length) return false
  return keys.every((k) => a[k] === b[k])
}

// El WS reenvía el pipeline COMPLETO en cada pipeline_step_changed (payload
// fresco de pydantic .model_dump()), aunque sólo haya cambiado un step. Sin
// esto, cada step object sería una referencia nueva en cada evento — inútil
// para React.memo en StepCard, que compara `step` por referencia.
function _reconcileSteps(prevSteps, nextSteps) {
  const prevById = new Map((prevSteps || []).map((s) => [s.step_id, s]))
  return nextSteps.map((step) => {
    const prev = prevById.get(step.step_id)
    return prev && _stepsEqual(prev, step) ? prev : step
  })
}

export const FACET_COLORS = {
  jax_local: '#3b82f6',
  jekyll:    '#6366f1',
  hyde:      '#f97316',
  hipatia:   '#10b981',
  thot:      '#f59e0b',
  kimi:      '#06b6d4',
  ada:       '#7c3aed',
  jacobs:    '#ffffff',
}

const DEFAULT_FACETS = Object.keys(FACET_COLORS).reduce((acc, name) => {
  acc[name] = { name, status: 'idle', last_message: '', color: FACET_COLORS[name] }
  return acc
}, {})

// Migración: el JWT y los datos de usuario vivían en localStorage (legible por XSS).
// Se purgan los restos de sesiones previas a este cambio.
localStorage.removeItem('jax_token')
localStorage.removeItem('jax_user')

export const useJaxStore = create((set, get) => ({
  token: null,
  user: null,
  sessionRestoring: true,
  facets: DEFAULT_FACETS,
  activePipelines: {},
  lasManos: false,
  wsStatus: 'disconnected',
  messages: [],
  toasts: [],
  killSwitchActive: false,
  activeFacet: 'jax_local',
  generatingImage: false,
  _pipelineCompletedShown: new Set(),

  restoreSession: async () => {
    try {
      const { data: refreshData } = await api.post('/auth/refresh')
      const { data: user } = await api.get('/auth/me', {
        headers: { Authorization: `Bearer ${refreshData.access_token}` },
      })
      set({ token: refreshData.access_token, user })
    } catch {
      set({ token: null, user: null })
    } finally {
      set({ sessionRestoring: false })
    }
  },

  login: async (email, password) => {
    const { data } = await api.post('/auth/login', { email, password })
    set({ token: data.access_token, user: data })
    return data
  },

  logout: () => {
    set({ token: null, user: null, messages: [] })
    api.post('/auth/logout').catch(() => {
      // best-effort: la sesión local ya quedó limpia
    })
  },

  setWsStatus: (wsStatus) => set({ wsStatus }),

  setActiveFacet: (facet) => set({ activeFacet: facet }),

  setGeneratingImage: (generatingImage) => set({ generatingImage }),

  handleEvent: (event) => {
    const { event_type, payload } = event

    if (event_type === 'facet_status_changed') {
      set((s) => {
        const update = {
          facets: {
            ...s.facets,
            [payload.facet]: {
              ...s.facets[payload.facet],
              status: payload.status,
              last_message: payload.message || '',
            },
          },
        }
        if (payload.status === 'thinking') {
          update.activeFacet = payload.facet
        }
        return update
      })
    }

    if (event_type === 'pipeline_step_changed') {
      set((s) => {
        const prevPipeline = s.activePipelines[payload.pipeline_id]
        const steps = _reconcileSteps(prevPipeline?.steps, payload.steps || [])
        return {
          activePipelines: {
            ...s.activePipelines,
            [payload.pipeline_id]: { ...payload, steps },
          },
        }
      })
    }

    if (event_type === 'las_manos_health_changed') {
      set({ lasManos: payload.alive })
    }

    if (event_type === 'kill_switch_activated') {
      set({ killSwitchActive: true })
      get().addToast({ type: 'error', message: 'KILL SWITCH ACTIVADO' })
    }

    if (event_type === 'human_gate_requested') {
      get().addToast({ type: 'warning', message: `Jacobs espera aprobación — pipeline ${payload.pipeline_id?.slice(0, 8)}` })
    }

    if (event_type === 'facet_response_completed') {
      set({ activeFacet: null })
    }

    if (event_type === 'command_completed') {
      const { task_id, result, result_preview, status } = payload
      const msgId = `cmd-${task_id}`
      const msgStatus = status === 'failed' ? 'failed' : 'completed'

      const applyResult = (content) => {
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === msgId ? { ...m, content, status: msgStatus } : m
          ),
        }))
        _savePendingIds(_loadPendingIds().filter(id => id !== task_id))
      }

      if (result) {
        applyResult(result)
      } else if (task_id) {
        // resultado completo en archivo — pedir al backend
        api.get(`/command/${task_id}`).then(({ data }) => {
          applyResult(data.result || result_preview || '(sin resultado)')
        }).catch(() => {
          applyResult(result_preview || '(sin resultado)')
        })
      } else {
        applyResult(result_preview || '(sin resultado)')
      }
    }

    if (event_type === 'pipeline_step_changed' && payload.status === 'completed') {
      const { pipeline_id } = payload
      const shown = get()._pipelineCompletedShown
      if (shown.has(pipeline_id)) return

      api.get(`/pipelines/${pipeline_id}/results`).then(({ data }) => {
        const allSteps = data.steps || []
        const completedSteps = allSteps.filter((s) => s.status === 'completed')
        const ts = new Date().toISOString()

        const newMessages = completedSteps.map((step) => {
          const header = `● **${step.facet}** — ${step.capability}`
          const body = step.result || '_(sin resultado)_'
          const sourceParts = (step.sources || []).map(
            (s) => `- [${s.title || s.url}](${s.url})`
          )
          const sourcesBlock = sourceParts.length
            ? `\n\n**Fuentes**\n${sourceParts.join('\n')}`
            : ''
          return {
            id: `pipeline-${pipeline_id}-step-${step.step_index}`,
            facet: step.facet,
            content: `${header}\n\n${body}${sourcesBlock}`,
            timestamp: ts,
          }
        })

        const secs = data.total_duration_seconds
          ? `, ${Math.round(data.total_duration_seconds)}s totales`
          : ''
        newMessages.push({
          id: `pipeline-${pipeline_id}-done`,
          facet: 'jacobs',
          content: `**Pipeline completado** — ${completedSteps.length} de ${allSteps.length} steps${secs}`,
          timestamp: ts,
        })

        set((s) => {
          if (s._pipelineCompletedShown.has(pipeline_id)) return s
          const existingIds = new Set(s.messages.map((m) => m.id))
          const toAppend = newMessages.filter((m) => !existingIds.has(m.id))
          return {
            _pipelineCompletedShown: new Set([...s._pipelineCompletedShown, pipeline_id]),
            ...(toAppend.length ? { messages: [...s.messages, ...toAppend] } : {}),
          }
        })
      }).catch(() => {})
    }
  },

  checkPendingTasks: async () => {
    const running = get().messages.filter(
      (m) => m.status === 'running' && m.id.startsWith('cmd-')
    )
    let stillRunning = 0
    for (const msg of running) {
      const taskId = msg.id.slice(4)
      try {
        const { data } = await api.get(`/command/${taskId}`)
        if (data.status === 'completed' && data.result) {
          set((s) => ({
            messages: s.messages.map((m) =>
              m.id === msg.id ? { ...m, content: data.result, status: 'completed' } : m
            ),
          }))
          _savePendingIds(_loadPendingIds().filter(id => id !== taskId))
        } else {
          stillRunning++
        }
      } catch {}
    }
    // si quedan tareas en curso, reintentar en 5s para capturar el resultado
    if (stillRunning > 0) {
      setTimeout(() => get().checkPendingTasks(), 5000)
    }
  },

  registerPendingCommand: (taskId) => {
    const ids = _loadPendingIds()
    if (!ids.includes(taskId)) _savePendingIds([...ids, taskId])
  },

  restorePendingTasks: () => {
    const ids = _loadPendingIds()
    if (!ids.length) return
    const ts = new Date().toISOString()
    set((s) => {
      const existingIds = new Set(s.messages.map((m) => m.id))
      const added = ids
        .filter((taskId) => !existingIds.has(`cmd-${taskId}`))
        .map((taskId) => ({
          id: `cmd-${taskId}`,
          facet: 'hyde',
          content: `_Tarea \`${taskId.slice(0, 8)}\` — verificando estado…_`,
          status: 'running',
          timestamp: ts,
        }))
      return added.length ? { messages: [...s.messages, ...added] } : s
    })
    get().checkPendingTasks()
  },

  addMessage: (msg) => set((s) =>
    s.messages.some((m) => m.id === msg.id)
      ? s
      : { messages: [...s.messages, msg] }
  ),

  updateMessage: (id, changes) => set((s) => ({
    messages: s.messages.map((m) => m.id === id ? { ...m, ...changes } : m),
  })),

  addToast: (toast) => {
    const id = Date.now()
    set((s) => ({ toasts: [...s.toasts, { ...toast, id }] }))
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
    }, 5000)
  },

  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

  activateKillSwitch: async () => {
    set({ killSwitchActive: true })
    try {
      await api.post('/kill-switch')
    } catch {}
    get().addToast({ type: 'error', message: 'KILL SWITCH ACTIVADO — todos los procesos detenidos' })
  },

  loadState: async () => {
    try {
      const { data } = await api.get('/state')
      set({
        facets: { ...DEFAULT_FACETS, ...data.facets },
        activePipelines: data.active_pipelines || {},
        lasManos: data.las_manos_alive,
      })
    } catch {}
  },
}))

export function getEyeState(facets, activePipelines, lasManos, killSwitchActive, generatingImage = false) {
  if (killSwitchActive) return { color: '#ef4444', animation: 'none', label: 'KILL SWITCH' }

  if (generatingImage) return { color: '#7c3aed', animation: 'pulse-fast', label: 'DALL-E 3' }

  // Thinking toma prioridad sobre todo — incluso si lasManos está abajo
  const thinking = Object.entries(facets).find(([, f]) => f.status === 'thinking')
  if (thinking) {
    const [name, f] = thinking
    const anim = name === 'hyde' ? 'pulse-fast' : 'pulse-slow'
    return { color: f.color, animation: anim, label: name }
  }

  if (!lasManos) return { color: '#374151', animation: 'none', label: 'LAS MANOS DOWN' }

  const hasGate = Object.values(activePipelines).some(p => p.status === 'waiting_gate')
  if (hasGate) return { color: '#f59e0b', animation: 'blink', label: 'GATE' }

  const hasRunning = Object.values(activePipelines).some(p => p.status === 'running')
  if (hasRunning) return { color: '#ffffff', animation: 'pulse-slow', label: 'Jacobs' }

  return { color: '#3b82f6', animation: 'pulse-slow', label: 'reposo' }
}
