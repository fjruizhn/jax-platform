import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { I18nProvider } from '../../i18n/index.jsx'

const invitations = vi.hoisted(() => ({ issue: vi.fn(), revoke: vi.fn() }))
vi.mock('../../api/virtualOffice', () => ({ officeInvitationsApi: invitations }))

import OfficeInvitationManager from './OfficeInvitationManager'

beforeEach(() => {
  vi.clearAllMocks()
  invitations.issue.mockResolvedValue({ data: {} })
  invitations.revoke.mockResolvedValue({ data: {} })
  vi.stubGlobal('crypto', { randomUUID: vi.fn(() => 'fixed-uuid') })
})

function show(props = {}) {
  return render(<I18nProvider><OfficeInvitationManager projectId={12} invitations={[{ id: 44, version: 3, email: 'ana@example.test' }]} enabled onChanged={vi.fn()} {...props} /></I18nProvider>)
}

describe('OfficeInvitationManager', () => {
  it('stays absent while the backend contract is not wired', () => {
    show({ enabled: false })
    expect(screen.queryByRole('button', { name: 'Gestionar invitaciones' })).not.toBeInTheDocument()
  })

  it('issues the typed audience with a UUID idempotency key', async () => {
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Gestionar invitaciones' }))
    fireEvent.change(screen.getByLabelText('Correo electrónico'), { target: { value: 'ana@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar invitación' }))
    await waitFor(() => expect(invitations.issue).toHaveBeenCalledWith(12, { email: 'ana@example.test', audience: 'INTERNAL' }, 'fixed-uuid'))
  })

  it('requires an explicit modal confirmation before revoking the version displayed', async () => {
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Gestionar invitaciones' }))
    fireEvent.click(screen.getByRole('button', { name: 'Revocar invitación para ana@example.test' }))
    expect(screen.getByRole('dialog', { name: 'Revocar invitación' })).toBeInTheDocument()
    expect(invitations.revoke).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Revocar invitación' }))
    await waitFor(() => expect(invitations.revoke).toHaveBeenCalledWith(12, 44, 3, 'fixed-uuid'))
  })
})
