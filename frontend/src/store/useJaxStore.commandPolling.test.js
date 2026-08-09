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

describe('checkPendingTasks resolves a task the backend no longer recognizes', () => {
  beforeEach(() => {
    useJaxStore.setState({ ...INITIAL_STATE, token: 'test-token', user: { user_id: 1 } }, true)
    vi.clearAllMocks()
  })

  it('resolves the placeholder and stops tracking it on a 404 (backend rejects/forgot the task_id)', async () => {
    useJaxStore.setState({
      messages: [{ id: 'cmd-t1', facet: 'hyde', content: '_verificando…_', status: 'running', timestamp: 't0' }],
    })
    localStorage.setItem('jax_pending_cmds', JSON.stringify({ owner: 1, ids: ['t1'] }))

    const err = new Error('not found')
    err.response = { status: 404 }
    api.get.mockRejectedValue(err)

    await useJaxStore.getState().checkPendingTasks()

    const msg = useJaxStore.getState().messages.find((m) => m.id === 'cmd-t1')
    expect(msg.status).toBe('completed')
    const stored = JSON.parse(localStorage.getItem('jax_pending_cmds'))
    expect(stored.ids).not.toContain('t1')
    localStorage.removeItem('jax_pending_cmds')
  })

  it('keeps retrying (does not resolve or purge) on a transient error like a network failure', async () => {
    vi.useFakeTimers()
    try {
      useJaxStore.setState({
        messages: [{ id: 'cmd-t1', facet: 'hyde', content: '_verificando…_', status: 'running', timestamp: 't0' }],
      })
      localStorage.setItem('jax_pending_cmds', JSON.stringify({ owner: 1, ids: ['t1'] }))
      api.get.mockRejectedValue(new Error('network down')) // sin .response -> no es 404/400

      await useJaxStore.getState().checkPendingTasks()

      // sigue 'running' y sigue en la lista de pendientes -> se reintentará
      const msg = useJaxStore.getState().messages.find((m) => m.id === 'cmd-t1')
      expect(msg.status).toBe('running')
      const stored = JSON.parse(localStorage.getItem('jax_pending_cmds'))
      expect(stored.ids).toContain('t1')

      // y efectivamente se reprograma un reintento en 5s
      await vi.advanceTimersByTimeAsync(5000)
      expect(api.get).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
      localStorage.removeItem('jax_pending_cmds')
    }
  })
})
