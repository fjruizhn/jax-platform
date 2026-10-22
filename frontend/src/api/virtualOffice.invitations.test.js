import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./client', () => ({ default: { post: vi.fn(), delete: vi.fn() } }))

import api from './client'
import { officeInvitationsApi } from './virtualOffice'

describe('office invitation client', () => {
  beforeEach(() => vi.clearAllMocks())

  it('issues a project invitation with a fresh idempotency key', async () => {
    await officeInvitationsApi.issue(12, { email: 'ana@example.test', audience: 'INTERNAL' }, 'idempotency-1')
    expect(api.post).toHaveBeenCalledWith('/virtual-office/projects/12/invitations', { email: 'ana@example.test', audience: 'INTERNAL' }, { headers: { 'Idempotency-Key': 'idempotency-1' } })
  })

  it('revokes the exact invitation version', async () => {
    await officeInvitationsApi.revoke(12, 44, 3, 'idempotency-2')
    expect(api.delete).toHaveBeenCalledWith('/virtual-office/projects/12/invitations/44', { params: { expected_version: 3 }, headers: { 'Idempotency-Key': 'idempotency-2' } })
  })

  it('binds an existing acceptance to its captured access token, never a later store token', async () => {
    await officeInvitationsApi.accept({ token: 'opaque-bearer' }, { accessToken: 'access-a' })
    expect(api.post).toHaveBeenCalledWith('/virtual-office/invitations/accept', { token: 'opaque-bearer' }, { headers: { Authorization: 'Bearer access-a' } })
  })

  it('posts a new-account bearer only in the JSON body and explicitly suppresses session auth', async () => {
    await officeInvitationsApi.accept({ token: 'opaque-bearer', password: 'new-password' }, { accessToken: null })
    expect(api.post).toHaveBeenCalledWith('/virtual-office/invitations/accept', { token: 'opaque-bearer', password: 'new-password' }, { headers: {}, _officeInvitationNoAuth: true })
  })
})
