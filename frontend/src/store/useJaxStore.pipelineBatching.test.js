import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  default: {
    post: vi.fn(),
    get: vi.fn(),
  },
}))

import api from '../api/client'
import { useJaxStore } from './useJaxStore'

const INITIAL_STATE = useJaxStore.getState()

describe('pipeline_step_changed completion batching', () => {
  beforeEach(() => {
    useJaxStore.setState(INITIAL_STATE, true)
    vi.clearAllMocks()
  })

  it('applies exactly 1 messages-reference change for a 3-step completed pipeline', async () => {
    api.get.mockResolvedValue({
      data: {
        steps: [
          { step_index: 0, facet: 'jekyll', capability: 'research', status: 'completed', result: 'r0' },
          { step_index: 1, facet: 'hyde', capability: 'write', status: 'completed', result: 'r1' },
          { step_index: 2, facet: 'thot', capability: 'review', status: 'completed', result: 'r2' },
        ],
        total_duration_seconds: 12.4,
      },
    })

    // Lo que le importa a CenterPanel.jsx (único suscriptor de `messages`,
    // vía selector `(s) => s.messages`) es cuántas veces cambia esa
    // referencia — no el total de notificaciones del store, que también
    // incluye el set() de `activePipelines` de la rama general de
    // pipeline_step_changed (no relacionado a este batching).
    let messagesRefChanges = 0
    let prevMessages = useJaxStore.getState().messages
    const unsubscribe = useJaxStore.subscribe((s) => {
      if (s.messages !== prevMessages) {
        messagesRefChanges++
        prevMessages = s.messages
      }
    })

    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_step_changed',
      payload: { pipeline_id: 'pid-1', status: 'completed' },
    })

    // deja correr el microtask del .then() de api.get
    await new Promise((resolve) => setTimeout(resolve, 0))
    unsubscribe()

    expect(messagesRefChanges).toBe(1)

    const { messages } = useJaxStore.getState()
    expect(messages.map((m) => m.id)).toEqual([
      'pipeline-pid-1-step-0',
      'pipeline-pid-1-step-1',
      'pipeline-pid-1-step-2',
      'pipeline-pid-1-done',
    ])
    expect(messages[3].content).toContain('3 de 3 steps')
  })

  it('does not re-fetch results for a duplicate completed event for the same pipeline', async () => {
    api.get.mockResolvedValue({
      data: {
        steps: [{ step_index: 0, facet: 'jekyll', capability: 'research', status: 'completed', result: 'r0' }],
        total_duration_seconds: 5,
      },
    })

    // Fire two events back-to-back (no await between) to test race guard before first fetch resolves
    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_step_changed',
      payload: { pipeline_id: 'pid-2', status: 'completed' },
    })

    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_step_changed',
      payload: { pipeline_id: 'pid-2', status: 'completed' },
    })

    // Now let both promises resolve
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(api.get).toHaveBeenCalledTimes(1)
    expect(useJaxStore.getState().messages).toHaveLength(2)
  })

  it('skips steps that are already in messages by id instead of duplicating them', async () => {
    api.get.mockResolvedValue({
      data: {
        steps: [{ step_index: 0, facet: 'jekyll', capability: 'research', status: 'completed', result: 'r0' }],
        total_duration_seconds: 3,
      },
    })
    useJaxStore.setState({
      messages: [{ id: 'pipeline-pid-3-step-0', facet: 'jekyll', content: 'ya estaba', timestamp: 't0' }],
    })

    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_step_changed',
      payload: { pipeline_id: 'pid-3', status: 'completed' },
    })
    await new Promise((resolve) => setTimeout(resolve, 0))

    const { messages } = useJaxStore.getState()
    expect(messages).toHaveLength(2)
    expect(messages[0].content).toBe('ya estaba')
    expect(messages[1].id).toBe('pipeline-pid-3-done')
  })

  it('collapses duplicate step ids within the same batch instead of appending both', async () => {
    api.get.mockResolvedValue({
      data: {
        // LAS MANOS repite step_index (payload malformado/duplicado) — no debe
        // producir dos mensajes con el mismo id ni un warning de key duplicada en React.
        steps: [
          { step_index: 0, facet: 'jekyll', capability: 'research', status: 'completed', result: 'r0-first' },
          { step_index: 0, facet: 'jekyll', capability: 'research', status: 'completed', result: 'r0-second' },
        ],
        total_duration_seconds: 2,
      },
    })

    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_step_changed',
      payload: { pipeline_id: 'pid-4', status: 'completed' },
    })
    await new Promise((resolve) => setTimeout(resolve, 0))

    const { messages } = useJaxStore.getState()
    const ids = messages.map((m) => m.id)
    expect(ids).toEqual(['pipeline-pid-4-step-0', 'pipeline-pid-4-done'])
    expect(messages[0].content).toContain('r0-first')
  })

  it('shows an error toast and allows a retry when the results fetch fails', async () => {
    api.get.mockRejectedValue(new Error('network down'))

    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_step_changed',
      payload: { pipeline_id: 'pid-5', status: 'completed' },
    })
    await new Promise((resolve) => setTimeout(resolve, 0))

    const { toasts, _pipelineCompletedShown, messages } = useJaxStore.getState()
    expect(toasts.some((t) => t.type === 'error')).toBe(true)
    expect(_pipelineCompletedShown.has('pid-5')).toBe(false)
    expect(messages).toHaveLength(0)

    // Un evento posterior para el mismo pipeline puede reintentar el fetch.
    api.get.mockResolvedValue({
      data: {
        steps: [{ step_index: 0, facet: 'jekyll', capability: 'research', status: 'completed', result: 'r0' }],
        total_duration_seconds: 1,
      },
    })
    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_step_changed',
      payload: { pipeline_id: 'pid-5', status: 'completed' },
    })
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(api.get).toHaveBeenCalledTimes(2)
    expect(useJaxStore.getState().messages.map((m) => m.id)).toEqual([
      'pipeline-pid-5-step-0',
      'pipeline-pid-5-done',
    ])
  })
})
