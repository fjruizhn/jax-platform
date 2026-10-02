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

describe('logout() does not leak session state into the next login', () => {
  beforeEach(() => {
    useJaxStore.setState({ ...INITIAL_STATE, token: 'test-token', user: { user_id: 1 } }, true)
    vi.clearAllMocks()
    api.post.mockResolvedValue({})
  })

  it('bumps the session epoch so a stale pipeline-results retry does not write into a session that logs back in', async () => {
    vi.useFakeTimers()
    try {
      api.get.mockRejectedValue(new Error('network down'))

      useJaxStore.getState().handleEvent({
        event_type: 'pipeline_step_changed',
        payload: { pipeline_id: 'pid-leak', status: 'completed' },
      })
      // deja que el primer intento (rechazado) programe el reintento de 2s
      await vi.advanceTimersByTimeAsync(0)
      expect(api.get).toHaveBeenCalledTimes(1)

      // logout + un nuevo login rápido en el mismo browser, antes de que
      // dispare el reintento — el token vuelve a ser truthy, así que un
      // simple "¿hay token?" no alcanzaría para distinguir esto de la
      // misma sesión.
      await useJaxStore.getState().logout()
      useJaxStore.setState((s) => ({
        token: 'new-session-token',
        user: { user_id: 2 },
        _sessionEpoch: s._sessionEpoch + 1,
      }))

      await vi.advanceTimersByTimeAsync(2000)

      // el reintento capturó el epoch de la sesión vieja -> ni siquiera
      // vuelve a llamar a la API, y no escribe nada en la sesión nueva
      expect(api.get).toHaveBeenCalledTimes(1)
      expect(useJaxStore.getState().messages).toHaveLength(0)
      expect(useJaxStore.getState().toasts.some((t) => t.type === 'error')).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })
})
