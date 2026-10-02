import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  default: { post: vi.fn(), get: vi.fn() },
}))

import api from '../api/client'
import { useJaxStore } from './useJaxStore'

const INICIAL = useJaxStore.getState()

// E1, T9 ronda 1 (I-1): el proyecto elegido del chat no sobrevive a una sesión
// que se pierde, sea cual sea el camino; si no, quien entra después en la misma
// pestaña lo heredaría sin haberlo elegido.
describe('proyectoActivo no sobrevive a la sesión que lo eligió', () => {
  beforeEach(() => {
    useJaxStore.setState({ ...INICIAL, token: 't', user: { user_id: 1 }, proyectoActivo: { id: 7, nombre: 'P' } }, true)
    vi.clearAllMocks()
  })

  it('logout lo deja en null', async () => {
    api.post.mockResolvedValue({})
    await useJaxStore.getState().logout()
    expect(useJaxStore.getState().proyectoActivo).toBeNull()
  })

  it('un restoreSession fallido lo deja en null', async () => {
    api.post.mockRejectedValue(new Error('sin sesión'))
    await useJaxStore.getState().restoreSession()
    expect(useJaxStore.getState().user).toBeNull()
    expect(useJaxStore.getState().proyectoActivo).toBeNull()
  })
})
