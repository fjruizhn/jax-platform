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

describe('restoreSession', () => {
  beforeEach(() => {
    useJaxStore.setState(INITIAL_STATE, true)
    vi.clearAllMocks()
  })

  it('never reads the JWT or user out of localStorage', async () => {
    const getItemSpy = vi.spyOn(Storage.prototype, 'getItem')
    api.post.mockResolvedValue({ data: { access_token: 'tok-123' } })
    api.get.mockResolvedValue({ data: { id: 1, email: 'a@b.com' } })

    await useJaxStore.getState().restoreSession()

    expect(getItemSpy).not.toHaveBeenCalledWith('jax_token')
    expect(getItemSpy).not.toHaveBeenCalledWith('jax_user')
  })

  it('sets token and user on a successful refresh', async () => {
    api.post.mockResolvedValue({ data: { access_token: 'tok-123' } })
    api.get.mockResolvedValue({ data: { id: 1, email: 'a@b.com' } })

    await useJaxStore.getState().restoreSession()

    const state = useJaxStore.getState()
    expect(state.token).toBe('tok-123')
    expect(state.user).toEqual({ id: 1, email: 'a@b.com' })
    expect(state.sessionRestoring).toBe(false)
  })

  it('clears token and user when the refresh fails', async () => {
    api.post.mockRejectedValue(new Error('401'))

    await useJaxStore.getState().restoreSession()

    const state = useJaxStore.getState()
    expect(state.token).toBeNull()
    expect(state.user).toBeNull()
    expect(state.sessionRestoring).toBe(false)
  })
})

// Mi cuenta (2026-09-12, admin usuarios etapa 4): cambiar la propia
// contraseña reemplaza `token` con el access nuevo que manda el backend --
// las otras sesiones quedan cerradas por su propio token_version.
describe('cambiarMiPassword', () => {
  beforeEach(() => {
    useJaxStore.setState(INITIAL_STATE, true)
    vi.clearAllMocks()
  })

  it('postea current/new password y reemplaza el token con el access nuevo', async () => {
    api.post.mockResolvedValue({ data: { access_token: 'tok-nuevo' } })

    await useJaxStore.getState().cambiarMiPassword('vieja-clave', 'nueva-clave-9')

    expect(api.post).toHaveBeenCalledWith('/auth/me/password', {
      current_password: 'vieja-clave', new_password: 'nueva-clave-9',
    })
    expect(useJaxStore.getState().token).toBe('tok-nuevo')
  })
})
