import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./client', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() } }))

import api from './client'
import { officeInvitationsApi, officeMembersApi } from './virtualOffice'

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

  it('reads and changes existing memberships with their server cursor and exact version', async () => {
    await officeMembersApi.list(12, { limit: 50, cursor: 9 })
    expect(api.get).toHaveBeenCalledWith('/virtual-office/projects/12/members', { params: { limit: 50, cursor: 9 } })
    await officeMembersApi.changeRole(12, 44, { grant_role: 'PROJECT_ADMIN', expected_version: 3 }, 'member-role')
    expect(api.patch).toHaveBeenCalledWith('/virtual-office/projects/12/members/44/role', { grant_role: 'PROJECT_ADMIN', expected_version: 3 }, { headers: { 'Idempotency-Key': 'member-role' } })
    await officeMembersApi.revoke(12, 44, 4, 'member-revoke')
    expect(api.delete).toHaveBeenCalledWith('/virtual-office/projects/12/members/44', { params: { expected_version: 4 }, headers: { 'Idempotency-Key': 'member-revoke' } })
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
