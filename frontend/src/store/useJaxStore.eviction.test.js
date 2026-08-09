import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  default: {
    post: vi.fn(),
    get: vi.fn(),
  },
}))

import { useJaxStore } from './useJaxStore'

const INITIAL_STATE = useJaxStore.getState()

describe('unbounded growth caps (long-session memory leaks)', () => {
  beforeEach(() => {
    useJaxStore.setState({ ...INITIAL_STATE, token: 'test-token', user: { user_id: 1 } }, true)
    vi.clearAllMocks()
  })

  afterEach(() => {
    localStorage.removeItem('jax_pending_cmds')
  })

  it('addMessage caps the messages array at 200, dropping the oldest first', () => {
    for (let i = 0; i < 205; i++) {
      useJaxStore.getState().addMessage({ id: `m${i}`, facet: 'jax_local', content: `msg ${i}`, timestamp: `t${i}` })
    }
    const { messages } = useJaxStore.getState()
    expect(messages).toHaveLength(200)
    expect(messages[0].id).toBe('m5') // los primeros 5 (m0..m4) se descartaron
    expect(messages[messages.length - 1].id).toBe('m204')
  })

  it('restorePendingTasks respects the same cap when adding many at once', () => {
    useJaxStore.setState({
      messages: Array.from({ length: 199 }, (_, i) => ({ id: `old-${i}`, facet: 'hyde', content: 'x', timestamp: 't' })),
    })
    localStorage.setItem('jax_pending_cmds', JSON.stringify({ owner: 1, ids: ['a', 'b', 'c'] }))

    useJaxStore.getState().restorePendingTasks()

    const { messages } = useJaxStore.getState()
    expect(messages).toHaveLength(200)
    // los 3 restaurados son los más nuevos -> sobreviven; se descartan los 2 más viejos
    expect(messages.filter((m) => m.id.startsWith('cmd-'))).toHaveLength(3)
  })

  it('never evicts a running message even if it is the oldest one', () => {
    // el más viejo de todos está 'running' (comando genuinamente pendiente
    // en el backend) — perderlo pierde su resultado para siempre.
    useJaxStore.getState().addMessage({ id: 'cmd-oldest', facet: 'hyde', content: '_pendiente_', status: 'running', timestamp: 't0' })
    for (let i = 0; i < 205; i++) {
      useJaxStore.getState().addMessage({ id: `m${i}`, facet: 'jax_local', content: `msg ${i}`, timestamp: `t${i}` })
    }

    const { messages } = useJaxStore.getState()
    // cap respetado (200 total: el 'running' + los 199 no-running más nuevos)
    expect(messages).toHaveLength(200)
    expect(messages.find((m) => m.id === 'cmd-oldest')).toBeDefined()
    expect(messages.find((m) => m.id === 'm0')).toBeUndefined() // descartado
    expect(messages.find((m) => m.id === 'm5')).toBeUndefined() // descartado
    expect(messages.find((m) => m.id === 'm6')).toBeDefined() // sobrevive
    expect(messages.find((m) => m.id === 'm204')).toBeDefined()
  })

  it('pipeline_step_changed(general) evicts the oldest finished pipeline once over the cap, never a running one', () => {
    const activePipelines = {}
    for (let i = 0; i < 50; i++) {
      activePipelines[`p${i}`] = { pipeline_id: `p${i}`, status: 'completed', steps: [] }
    }
    // la más vieja de todas (p0) queda corriendo -> no debe evictarse aunque sea la más antigua
    activePipelines['p0'].status = 'running'
    useJaxStore.setState({ activePipelines })

    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_step_changed',
      payload: { pipeline_id: 'p50', status: 'running', steps: [] },
    })

    const result = useJaxStore.getState().activePipelines
    expect(Object.keys(result)).toHaveLength(50)
    expect(result['p0']).toBeDefined() // corriendo, protegida
    expect(result['p1']).toBeUndefined() // la más vieja *terminada* fue evictada
    expect(result['p50']).toBeDefined() // la nueva entró
  })

  it('reinserts an updated pipeline at the end of eviction order instead of leaving it at its original slot', () => {
    const activePipelines = {}
    for (let i = 0; i < 50; i++) {
      activePipelines[`p${i}`] = { pipeline_id: `p${i}`, status: 'completed', steps: [] }
    }
    useJaxStore.setState({ activePipelines })

    // p0 es la más vieja por orden de inserción, pero "acaba de actualizarse"
    // (p.ej. falló recién ahora) — no debería ser la primera en evictarse
    // sólo por haber sido la primera en insertarse alguna vez. Se usa
    // 'failed' (no 'completed') para no disparar la rama separada que
    // hace fetch de resultados al completarse.
    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_step_changed',
      payload: { pipeline_id: 'p0', status: 'failed', steps: [] },
    })
    // una pipeline nueva empuja el total a 51 -> dispara eviction
    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_step_changed',
      payload: { pipeline_id: 'p50', status: 'running', steps: [] },
    })

    const result = useJaxStore.getState().activePipelines
    expect(result['p0']).toBeDefined() // reinsertada al final -> no es la más vieja
    expect(result['p1']).toBeUndefined() // ahora p1 es la terminada más vieja -> evictada
  })
})
