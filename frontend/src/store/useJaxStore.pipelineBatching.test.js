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

  it('applies at most 2 store notifications for a 3-step completed pipeline', async () => {
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

    let notifications = 0
    const unsubscribe = useJaxStore.subscribe(() => { notifications++ })

    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_step_changed',
      payload: { pipeline_id: 'pid-1', status: 'completed' },
    })

    // deja correr el microtask del .then() de api.get
    await new Promise((resolve) => setTimeout(resolve, 0))
    unsubscribe()

    expect(notifications).toBeLessThanOrEqual(3)

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
})
