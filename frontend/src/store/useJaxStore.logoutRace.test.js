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

describe('async writers do not resurrect a logged-out session', () => {
  beforeEach(() => {
    useJaxStore.setState({ ...INITIAL_STATE, token: 'test-token' }, true)
    vi.clearAllMocks()
  })

  it('does not apply a command_completed result if the session logged out mid-fetch', async () => {
    useJaxStore.setState({
      messages: [{ id: 'cmd-t1', facet: 'hyde', content: '_verificando…_', status: 'running', timestamp: 't0' }],
    })
    api.get.mockResolvedValue({ data: { result: 'resultado final' } })

    useJaxStore.getState().handleEvent({
      event_type: 'command_completed',
      payload: { task_id: 't1', result: null, result_preview: 'preview', status: 'completed' },
    })

    // logout mientras el fetch de /command/t1 está en vuelo
    useJaxStore.setState({ token: null })
    await new Promise((resolve) => setTimeout(resolve, 0))

    const msg = useJaxStore.getState().messages.find((m) => m.id === 'cmd-t1')
    expect(msg.status).toBe('running')
    expect(msg.content).toBe('_verificando…_')
  })

  it('command_completed does nothing at all when the session is already logged out', async () => {
    useJaxStore.setState({ token: null, messages: [] })
    api.get.mockResolvedValue({ data: { result: 'no debería aplicarse' } })

    useJaxStore.getState().handleEvent({
      event_type: 'command_completed',
      payload: { task_id: 't1', result: 'resultado directo', result_preview: null, status: 'completed' },
    })
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(useJaxStore.getState().messages).toHaveLength(0)
  })

  it('checkPendingTasks stops and does not write results once the session logs out mid-loop', async () => {
    useJaxStore.setState({
      messages: [
        { id: 'cmd-t1', status: 'running', timestamp: 't0' },
        { id: 'cmd-t2', status: 'running', timestamp: 't0' },
      ],
    })

    let resolveFirst
    api.get.mockImplementation((url) => {
      if (url === '/command/t1') {
        return new Promise((resolve) => { resolveFirst = resolve })
      }
      return Promise.resolve({ data: { status: 'completed', result: 'r2' } })
    })

    const pending = useJaxStore.getState().checkPendingTasks()

    // logout mientras el fetch de la primera tarea sigue en vuelo
    useJaxStore.setState({ token: null })
    resolveFirst({ data: { status: 'completed', result: 'r1' } })
    await pending

    expect(api.get).toHaveBeenCalledTimes(1) // nunca llega a pedir cmd-t2
    const messages = useJaxStore.getState().messages
    expect(messages.find((m) => m.id === 'cmd-t1').status).toBe('running')
    expect(messages.find((m) => m.id === 'cmd-t2').status).toBe('running')
  })

  it('checkPendingTasks does not reschedule polling after logout', async () => {
    vi.useFakeTimers()
    try {
      useJaxStore.setState({
        messages: [{ id: 'cmd-t1', status: 'running', timestamp: 't0' }],
      })
      api.get.mockResolvedValue({ data: { status: 'running' } })

      const pending = useJaxStore.getState().checkPendingTasks()
      useJaxStore.setState({ token: null })
      await pending

      await vi.advanceTimersByTimeAsync(5000)

      // sin reintento: sigue en 1 sola llamada
      expect(api.get).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })
})
