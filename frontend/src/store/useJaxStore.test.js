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
