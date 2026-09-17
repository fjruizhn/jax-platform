import { create } from 'zustand'
import api from '../api/client'
import es from '../i18n/es.js'
import en from '../i18n/en.js'
import { EYE_ESTADO_REPOSO } from './eyeRestState'
import { tokenDeFaceta } from '../tema/tokens'

// Este módulo no es un componente — no puede usar el hook useI18n(). Lee la
// misma fuente que I18nProvider (localStorage 'jax_lang') para los mensajes
// que se generan acá (eventos de WS), fuera de cualquier árbol de React.
function _t() {
  return localStorage.getItem('jax_lang') === 'en' ? en : es
}

// A-53 (2026-09-16): el resultado de un comando llega con código cuando no hay
// texto que mostrar (sin output, fallo o simulación). Lo usan el evento de WS
// y la consulta de pendientes: un solo lugar decide el texto.
export function contenidoDeComando(t, datos) {
  if (datos?.code === 'comando_fallo') return t.commandFailed(datos.motivo || '')
  if (datos?.code === 'comando_simulado') return t.commandDryRun(datos.result || '')
  return datos?.result || t.commandNoResult
}

const RESULTS_FETCH_MAX_ATTEMPTS = 2
// Tope del POST /auth/logout: salir nunca espera más que esto a la red.
const LOGOUT_TIMEOUT_MS = 5000
const RESULTS_FETCH_RETRY_DELAY_MS = 2000

// Cotas de memoria para sesiones largas — sin esto, `messages` y
// `activePipelines` crecen sin límite durante toda la vida de la pestaña.
const MAX_MESSAGES = 200
const MAX_TRACKED_PIPELINES = 50

// Nunca descarta un mensaje 'running' (comando/tarea todavía en curso en el
// backend) aunque sea el más viejo — perderlo acá pierde el resultado para
// siempre (ver applyResult, que sólo lo escribe si el placeholder sigue en
// `messages`). Sólo recorta entre los mensajes ya resueltos.
function _capMessages(messages) {
  if (messages.length <= MAX_MESSAGES) return messages
  let toDrop = messages.length - MAX_MESSAGES
  return messages.filter((m) => {
    if (toDrop > 0 && m.status !== 'running') {
      toDrop--
      return false
    }
    return true
  })
}

// Descarta las pipelines más viejas que ya terminaron (nunca una corriendo
// o esperando aprobación) cuando se supera la cota — el backend limita a 3
// pipelines concurrentes por tenant, así que esto nunca compite con una
// pipeline activa real.
function _evictOldFinishedPipelines(pipelines) {
  const ids = Object.keys(pipelines)
  if (ids.length <= MAX_TRACKED_PIPELINES) return pipelines
  const finishedIds = ids.filter((id) => !['running', 'waiting_gate'].includes(pipelines[id].status))
  const toEvict = finishedIds.slice(0, ids.length - MAX_TRACKED_PIPELINES)
  if (!toEvict.length) return pipelines
  const next = { ...pipelines }
  for (const id of toEvict) delete next[id]
  return next
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

// Token de identidad de cada faceta (spec 2026-09-14-tema-tokens §7.3): el
// store guarda el NOMBRE del token, no un hex; quien pinta usa colorToken().
export const FACET_TOKENS = Object.fromEntries(
  ['jax_local', 'jekyll', 'hyde', 'hipatia', 'thot', 'kimi', 'ada', 'jacobs'].map((n) => [n, tokenDeFaceta(n)]),
)

const DEFAULT_FACETS = Object.keys(FACET_TOKENS).reduce((acc, name) => {
  acc[name] = { name, status: 'idle', last_message: '', token: FACET_TOKENS[name] }
  return acc
}, {})

// Faceta que entra desde el servidor (/api/state o facet_status_changed). El
// backend manda {name, status, last_message, last_update, color} SIN token:
// el token se deriva SIEMPRE de la clave (nunca de los datos del servidor) y
// el `color` hex del backend se descarta -- nada pinta con él. Lo demás se
// fusiona sobre el default de la faceta (o sobre lo que ya había).
function _facetaDelServidor(clave, base, datos) {
  const { color: _hexDelBackend, ...resto } = datos || {}
  return { ...DEFAULT_FACETS[clave], ...base, ...resto, token: tokenDeFaceta(clave) }
}

// Migración: el JWT y los datos de usuario vivían en localStorage (legible por XSS).
// Se purgan los restos de sesiones previas a este cambio.
localStorage.removeItem('jax_token')
localStorage.removeItem('jax_user')

export const useJaxStore = create((set, get) => {
  // Varios escritores async (fetch de resultados de pipeline, polling de
  // comandos pendientes) programan su propio setTimeout/then() que puede
  // resolver bien después de logout(), bien después de que OTRO usuario se
  // loguee en el mismo browser — un simple "¿hay token?" no distingue esos
  // dos casos, porque el nuevo login también deja un token truthy. Cada
  // cambio de sesión (login/logout/restoreSession) incrementa
  // `_sessionEpoch`; los escritores capturan el epoch vigente al programar
  // su trabajo y sólo escriben si sigue siendo el mismo al resolver.
  const bumpSessionEpoch = () => set((s) => ({ _sessionEpoch: s._sessionEpoch + 1 }))
  const isSameSession = (epoch) => get()._sessionEpoch === epoch && !!get().token

  // jax_pending_cmds está scopeado por dueño (user_id), no sólo limpiado en
  // logout(): la sesión también puede terminar por un refresh silencioso
  // que falla (api/client.js) o por restoreSession() al cargar la página, y
  // perseguir cada uno de esos puntos es frágil. Con el owner embebido, un
  // login de OTRO usuario en el mismo browser simplemente no matchea y lee
  // vacío — sin importar por dónde terminó la sesión anterior.
  const _loadPendingIds = () => {
    try {
      const raw = JSON.parse(localStorage.getItem('jax_pending_cmds') || 'null')
      if (!raw || raw.owner !== (get().user?.user_id ?? null) || !Array.isArray(raw.ids)) return []
      return raw.ids
    } catch { return [] }
  }
  const _savePendingIds = (ids) => {
    localStorage.setItem('jax_pending_cmds', JSON.stringify({ owner: get().user?.user_id ?? null, ids }))
  }
  // A-44 (2026-09-16): "resolver un comando" (contenido, estado, sacarlo de
  // pendientes) en un solo lugar. Quien llama conserva sus chequeos (sesión
  // vigente, mensaje que todavía existe) ANTES de llamarlo.
  const _resolverComando = (msgId, taskId, content, status) => {
    set((s) => ({ messages: s.messages.map((m) => (m.id === msgId ? { ...m, content, status } : m)) }))
    _savePendingIds(_loadPendingIds().filter((id) => id !== taskId))
  }

  return {
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
  _sessionEpoch: 0,
  // Motivo por el que se cerró la sesión (clave de i18n) o null. Lo escribe
  // api/client.js ANTES de borrar token/user cuando el refresh silencioso
  // falla — nunca se borra la sesión sin dejar el motivo. Login.jsx lo
  // muestra y lo borra tras un login exitoso.
  avisoSesion: null,
  // Promesa del POST /auth/me/password mientras está en vuelo, o null (minor 5
  // del review final de la etapa 4, 2026-09-15). api/client.js la espera ante
  // un 401 para reintentar con el token nuevo en vez de llamar a /auth/refresh
  // con la cookie vieja (que ya no vale tras subir token_version).
  cambioDePasswordEnCurso: null,
  // Promesa del logout mientras está en vuelo, o null (fix round 1 del review
  // de dd47d82). Un segundo logout() la reusa (un doble clic no envía dos
  // POST), los botones de salir quedan ocupados, y api/client.js no convierte
  // en aviso un 401 que llegue mientras tanto.
  saliendo: null,

  restoreSession: async () => {
    try {
      const { data: refreshData } = await api.post('/auth/refresh')
      const { data: user } = await api.get('/auth/me', {
        headers: { Authorization: `Bearer ${refreshData.access_token}` },
      })
      set({ token: refreshData.access_token, user })
      bumpSessionEpoch()
    } catch {
      set({ token: null, user: null })
      bumpSessionEpoch()
    } finally {
      set({ sessionRestoring: false })
    }
  },

  login: async (email, password) => {
    const { data } = await api.post('/auth/login', { email, password })
    set({ token: data.access_token, user: data })
    bumpSessionEpoch()
    return data
  },

  // Login.jsx la llama tras un login exitoso -- separada de login() porque
  // Login.test.jsx mockea login() como función suelta (vi.fn()), así que la
  // limpieza del aviso tiene que ser una responsabilidad explícita y propia
  // del componente, no un efecto secundario escondido dentro de login().
  clearAvisoSesion: () => set({ avisoSesion: null }),

  // Sesión única (2026-09-15, Ruling F2): /auth/logout mata la sesión EN EL
  // SERVIDOR (sube token_version por la cookie de refresh) y siempre responde
  // 200. El pedido va ANTES de limpiar: si no, un login rápido en la misma
  // pestaña podía recibir su cookie nueva y después el delete_cookie de este
  // logout. Si el pedido falla (red), la sesión local se limpia igual y no se
  // reintenta (/auth/logout está en ENDPOINTS_DE_AUTH_SIN_REINTENTO). El
  // timeout evita que una red colgada deje a la persona sin poder salir.
  //
  // Fix round 1 (review de dd47d82): una sola salida en vuelo (`saliendo`), y
  // avisoSesion se borra -- un poll que llegó al servidor después del logout
  // no debe dejar en Login "se inició sesión en otro lugar".
  logout: () => {
    const enCurso = get().saliendo
    if (enCurso) return enCurso
    const promesa = (async () => {
      try {
        await api.post('/auth/logout', {}, { timeout: LOGOUT_TIMEOUT_MS })
      } catch {
        // sin sesión viva en el servidor o sin red: igual se sale localmente
      }
      set({ token: null, user: null, messages: [], _pipelineCompletedShown: new Set(), avisoSesion: null, saliendo: null })
      bumpSessionEpoch()
    })()
    set({ saliendo: promesa })
    return promesa
    // No hace falta limpiar jax_pending_cmds acá a mano: está scopeado por
    // owner (ver _loadPendingIds arriba), así que un login de otro usuario
    // ya lo lee vacío solo. Borrarlo acá de más perdería, sin necesidad, los
    // comandos pendientes propios de ESTE usuario si vuelve a loguearse.
  },

  // Mi cuenta (2026-09-12, admin usuarios etapa 4): el backend sube la versión
  // de token -- cierra las OTRAS sesiones -- y le da a esta un access nuevo (y
  // la cookie de refresh nueva). Cambiar `token` reconecta el WebSocket con él
  // (useWebSocket depende de token).
  cambiarMiPassword: async (actual, nueva) => {
    // El token nuevo se guarda DENTRO de la promesa: quien la espera (el
    // interceptor) ya lo encuentra en el store al despertar.
    const promesa = api.post('/auth/me/password', { current_password: actual, new_password: nueva })
      .then(({ data }) => {
        // U34: el backend apaga must_change_password en el MISMO UPDATE que
        // cambia el hash (api/auth.py, /me/password) y responde sólo el token
        // (RefreshResponse, sin la marca). Acá se apaga igual, sin pedir /me:
        // RequireAuth vuelve a montar la app.
        const user = get().user
        set({ token: data.access_token, user: user ? { ...user, must_change_password: false } : user })
      })
    set({ cambioDePasswordEnCurso: promesa })
    try {
      await promesa
    } finally {
      if (get().cambioDePasswordEnCurso === promesa) set({ cambioDePasswordEnCurso: null })
    }
  },

  // Etapa 5 (2026-09-15): el admin puede editar correos desde Usuarios. El JWT
  // no lleva el correo (verificar_sesion lo relee en cada request), pero la
  // barra de usuario muestra user.email del store: si el correo editado es el
  // del usuario logueado, se actualiza acá con el valor guardado; si es de
  // otro, no se toca nada.
  actualizarMiEmail: (userId, email) => {
    const user = get().user
    if (user && user.user_id === userId) set({ user: { ...user, email } })
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
            [payload.facet]: _facetaDelServidor(payload.facet, s.facets[payload.facet], {
              status: payload.status,
              last_message: payload.message || '',
            }),
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
        // delete + set (no sólo sobreescribir) para que la key pase al final
        // del orden de inserción — _evictOldFinishedPipelines lee ese orden
        // como "más vieja primero", y una key existente reasignada in-place
        // NO se mueve de posición en JS.
        const activePipelines = { ...s.activePipelines }
        delete activePipelines[payload.pipeline_id]
        activePipelines[payload.pipeline_id] = { ...payload, steps }
        return { activePipelines: _evictOldFinishedPipelines(activePipelines) }
      })
    }

    if (event_type === 'las_manos_health_changed') {
      set({ lasManos: payload.alive })
    }

    if (event_type === 'kill_switch_activated') {
      set({ killSwitchActive: true })
      get().addToast({ type: 'error', message: _t().killSwitchToast })
    }

    if (event_type === 'human_gate_requested') {
      get().addToast({ type: 'warning', message: _t().humanGateRequestedToast(payload.pipeline_id?.slice(0, 8)) })
    }

    if (event_type === 'facet_response_completed') {
      set({ activeFacet: null })
    }

    if (event_type === 'command_completed') {
      const { task_id, status } = payload
      const msgId = `cmd-${task_id}`
      const msgStatus = status === 'failed' ? 'failed' : 'completed'
      const sessionEpoch = get()._sessionEpoch

      const applyResult = (content) => {
        if (!isSameSession(sessionEpoch)) return
        // El placeholder pudo ser evictado por _capMessages (sesión muy
        // larga) — no purgar el id pendiente en ese caso: así
        // restorePendingTasks lo recupera en el próximo reload en vez de
        // perder el resultado para siempre.
        if (!get().messages.some((m) => m.id === msgId)) return
        _resolverComando(msgId, task_id, content, msgStatus)
      }

      if (payload.result || payload.code) {
        applyResult(contenidoDeComando(_t(), payload))
      } else if (task_id) {
        // resultado completo en archivo — pedir al backend. Los dos
        // argumentos de .then() separan "el fetch falló" (sin resultado)
        // de "el fetch anduvo pero applyResult tiró" (bug real, no debe
        // aplicar "sin resultado" como si fuera la respuesta válida).
        api.get(`/command/${task_id}`).then(
          ({ data }) => applyResult(contenidoDeComando(_t(), data)),
          () => applyResult(_t().commandNoResult)
        ).catch((err) => console.error('command result render failed', err))
      } else {
        applyResult(_t().commandNoResult)
      }
    }

    if (event_type === 'pipeline_step_changed' && payload.status === 'completed') {
      const { pipeline_id } = payload
      const shown = get()._pipelineCompletedShown
      if (shown.has(pipeline_id)) return
      set((s) => ({ _pipelineCompletedShown: new Set([...s._pipelineCompletedShown, pipeline_id]) }))
      const sessionEpoch = get()._sessionEpoch

      // El backend emite este evento una sola vez y descarta el pipeline
      // (jax_engine/state.py remove_pipeline) — no hay un segundo evento que
      // permita reintentar más tarde. Por eso el fetch se reintenta acá mismo
      // antes de rendirse; el mark sólo se libera (para permitir un reintento
      // manual futuro, si alguna vez existe un disparador) tras agotar los intentos.
      const onFetchFailure = (attempt) => {
        if (!isSameSession(sessionEpoch)) return
        if (attempt < RESULTS_FETCH_MAX_ATTEMPTS) {
          setTimeout(() => fetchResults(attempt + 1), RESULTS_FETCH_RETRY_DELAY_MS)
          return
        }
        set((s) => {
          const next = new Set(s._pipelineCompletedShown)
          next.delete(pipeline_id)
          return { _pipelineCompletedShown: next }
        })
        get().addToast({
          type: 'error',
          message: _t().pipelineResultsError(pipeline_id?.slice(0, 8)),
        })
      }

      const fetchResults = (attempt) => {
        // La sesión pudo cerrarse (o cambiar a otro login) mientras este
        // reintento estaba pendiente (setTimeout sobrevive al logout) — no
        // reanudar con un fetch de una sesión que ya no es la vigente.
        if (!isSameSession(sessionEpoch)) return

        api.get(`/pipelines/${pipeline_id}/results`).then(({ data }) => {
          // Payload 200 pero sin forma válida (p.ej. LAS MANOS devuelve un
          // error con status 200) — se trata como fallo de fetch, no como
          // bug de renderizado.
          if (!Array.isArray(data?.steps)) {
            onFetchFailure(attempt)
            return
          }

          // Si un step_index viene repetido (payload malformado/duplicado),
          // se prefiere la copia 'completed' sobre cualquier otra —
          // descartar el resultado real sería peor que el duplicado.
          const byStepIndex = new Map()
          for (const step of data.steps) {
            const existing = byStepIndex.get(step.step_index)
            if (!existing || (existing.status !== 'completed' && step.status === 'completed')) {
              byStepIndex.set(step.step_index, step)
            }
          }
          const allSteps = [...byStepIndex.values()]
          const completedSteps = allSteps.filter((s) => s.status === 'completed')
          const ts = new Date().toISOString()

          const t = _t()
          const newMessages = completedSteps.map((step) => {
            const header = t.pipelineStepHeader(step.facet, step.capability)
            const body = step.result || t.pipelineNoResult
            const sourceParts = (step.sources || []).map(
              (s) => `- [${s.title || s.url}](${s.url})`
            )
            const sourcesBlock = sourceParts.length
              ? `\n\n**${t.pipelineSources}**\n${sourceParts.join('\n')}`
              : ''
            return {
              id: `pipeline-${pipeline_id}-step-${step.step_index}`,
              facet: step.facet,
              content: `${header}\n\n${body}${sourcesBlock}`,
              timestamp: ts,
            }
          })

          newMessages.push({
            id: `pipeline-${pipeline_id}-done`,
            facet: 'jacobs',
            content: t.pipelineCompleted(completedSteps.length, allSteps.length, data.total_duration_seconds),
            timestamp: ts,
          })

          // Re-chequeo tras el await: la sesión pudo cerrarse (o cambiar)
          // mientras el fetch estaba en vuelo.
          if (!isSameSession(sessionEpoch)) return

          set((s) => {
            const existingIds = new Set(s.messages.map((m) => m.id))
            const toAppend = newMessages.filter((m) => !existingIds.has(m.id))
            return toAppend.length ? { messages: _capMessages([...s.messages, ...toAppend]) } : s
          })
        }, () => onFetchFailure(attempt))
          // Cubre sólo bugs reales al construir los mensajes (no el fetch
          // en sí, ya manejado arriba) — se loguea y no se reintenta: un
          // toast de "resultados" sería engañoso para un bug de render, y
          // reintentar no lo arregla.
          .catch((err) => console.error('pipeline results render failed', err))
      }

      fetchResults(1)
    }
  },

  checkPendingTasks: async () => {
    const sessionEpoch = get()._sessionEpoch
    if (!isSameSession(sessionEpoch)) return
    const running = get().messages.filter(
      (m) => m.status === 'running' && m.id.startsWith('cmd-')
    )
    let stillRunning = 0
    for (const msg of running) {
      if (!isSameSession(sessionEpoch)) return
      const taskId = msg.id.slice(4)
      try {
        const { data } = await api.get(`/command/${taskId}`)
        if (!isSameSession(sessionEpoch)) return
        // A-44: un fallo o un completado sin texto (con código) también
        // resuelven; antes solo `completed` con `result` salía de "running".
        if (data.status === 'completed' || data.status === 'failed') {
          _resolverComando(msg.id, taskId, contenidoDeComando(_t(), data), data.status)
        } else {
          stillRunning++
        }
      } catch (err) {
        const httpStatus = err.response?.status
        if (httpStatus === 404 || httpStatus === 400) {
          // El backend ya no reconoce este task_id como propio (ownership
          // check del endpoint, tarea muy vieja ya limpiada, etc.) — sin
          // esto, restorePendingTasks() lo recrea como placeholder
          // "verificando estado…" para siempre en cada reload, un zombie
          // que nunca se resuelve. Se resuelve acá y se saca de la lista.
          _resolverComando(msg.id, taskId, _t().commandNoResult, 'completed')
        } else {
          stillRunning++ // error transitorio (red, 5xx) — reintentar en el próximo ciclo
        }
      }
    }
    // si quedan tareas en curso, reintentar en 5s para capturar el resultado.
    // No hace falta re-chequear la sesión acá: no hubo ningún await desde el
    // último chequeo dentro del loop, así que sigue siendo válida en este
    // mismo tick — y la llamada reprogramada vuelve a capturar y validar su
    // propio epoch al entrar, cubriendo un logout que ocurra en esos 5s.
    if (stillRunning > 0) {
      setTimeout(() => get().checkPendingTasks(), 5000)
    }
  },

  // sessionEpoch: capturado por el llamador ANTES de lanzar el POST que
  // produjo este taskId (ver BottomBar.jsx) — si la sesión cambió mientras
  // ese POST estaba en vuelo, no hay que registrar el id bajo la sesión
  // nueva (owner-scoping en _savePendingIds ya evita que otro usuario lo
  // lea, pero esto además evita pisarle a la sesión nueva su propia lista).
  registerPendingCommand: (taskId, sessionEpoch) => {
    if (!isSameSession(sessionEpoch)) return
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
          content: _t().taskRestoring(taskId.slice(0, 8)),
          status: 'running',
          timestamp: ts,
        }))
      return added.length ? { messages: _capMessages([...s.messages, ...added]) } : s
    })
    get().checkPendingTasks()
  },

  addMessage: (msg) => set((s) =>
    s.messages.some((m) => m.id === msg.id)
      ? s
      : { messages: _capMessages([...s.messages, msg]) }
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
    get().addToast({ type: 'error', message: _t().killSwitchStoppedToast })
  },

  loadState: async () => {
    try {
      const { data } = await api.get('/state')
      set({
        facets: {
          ...DEFAULT_FACETS,
          ...Object.fromEntries(
            Object.entries(data.facets || {}).map(([k, v]) => [k, _facetaDelServidor(k, undefined, v)]),
          ),
        },
        activePipelines: _evictOldFinishedPipelines(data.active_pipelines || {}),
        lasManos: data.las_manos_alive,
      })
    } catch {}
  },
  }
})

// M5 (revisión de código, 2026-09-14, fix vivo): las etiquetas visibles
// (KILL SWITCH, DALL-E 3, LAS MANOS DOWN, GATE, Jacobs) venían escritas a
// mano acá dentro -- hardcoding de i18n, igual que idleLabel antes de pasar
// a ser parámetro. `labels` sigue el mismo patrón: quien llama (HalEye.jsx)
// las pasa desde t.eye*; sin el parámetro caen en el mismo texto de
// siempre, así que una llamada vieja (o un test) que no lo pase no cambia
// de comportamiento. Son nombres propios/técnicos del ecosistema JAX, no
// prosa -- i18n/es.js y en.js documentan por qué valen igual en los dos
// idiomas.
export function getEyeState(
  facets, activePipelines, lasManos, killSwitchActive, generatingImage = false,
  idleLabel = 'reposo', labels = {},
) {
  const {
    killSwitch = 'KILL SWITCH',
    dalle = 'DALL-E 3',
    lasManosDown = 'LAS MANOS DOWN',
    gate = 'GATE',
    jacobs = 'Jacobs',
  } = labels

  if (killSwitchActive) return { token: 'peligro', animation: 'none', label: killSwitch }

  if (generatingImage) return { token: 'faceta-imagen', animation: 'pulse-fast', label: dalle }

  // Thinking toma prioridad sobre todo — incluso si lasManos está abajo
  const thinking = Object.entries(facets).find(([, f]) => f.status === 'thinking')
  if (thinking) {
    const [name, f] = thinking
    const anim = name === 'hyde' ? 'pulse-fast' : 'pulse-slow'
    return { token: f.token, animation: anim, label: name }
  }

  if (!lasManos) return { token: 'texto-tenue', animation: 'none', label: lasManosDown }

  const hasGate = Object.values(activePipelines).some(p => p.status === 'waiting_gate')
  if (hasGate) return { token: 'aviso', animation: 'blink', label: gate }

  const hasRunning = Object.values(activePipelines).some(p => p.status === 'running')
  if (hasRunning) return { token: 'faceta-jacobs', animation: 'pulse-slow', label: jacobs }

  return { ...EYE_ESTADO_REPOSO, label: idleLabel }
}
