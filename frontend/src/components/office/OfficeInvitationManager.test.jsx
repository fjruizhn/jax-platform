import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom'
import { I18nProvider } from '../../i18n/index.jsx'

const invitations = vi.hoisted(() => ({ list: vi.fn(), issue: vi.fn(), revoke: vi.fn() }))
vi.mock('../../api/virtualOffice', () => ({ officeInvitationsApi: invitations }))

import OfficeInvitationManager from './OfficeInvitationManager'

const firstPage = { data: { invitations: [{ id: '550e8400-e29b-41d4-a716-446655440000', version: 3, email: 'ana@example.test', audience: 'INTERNAL', status: 'ISSUED', expires_at: '2026-10-23T12:00:00Z' }], next_cursor: null } }

beforeEach(() => {
  vi.clearAllMocks()
  invitations.list.mockResolvedValue(firstPage)
  invitations.issue.mockResolvedValue({ data: {} })
  invitations.revoke.mockResolvedValue({ data: {} })
  vi.stubGlobal('crypto', { randomUUID: vi.fn(() => 'fixed-uuid') })
})

function show(props = {}) {
  return render(<I18nProvider><OfficeInvitationManager projectId={12} onForbidden={vi.fn()} {...props} /></I18nProvider>)
}

describe('OfficeInvitationManager', () => {
  it('loads only the server list with its opaque cursor and shows the active invitation', async () => {
    show()
    await waitFor(() => expect(invitations.list).toHaveBeenCalledWith(12, { limit: 25, cursor: null }))
    fireEvent.click(screen.getByRole('button', { name: 'Gestionar invitaciones' }))
    expect(await screen.findByText('ana@example.test')).toBeInTheDocument()
    expect(screen.getByText('Estado: ISSUED')).toBeInTheDocument()
  })

  it('keeps the cursor opaque and appends only the page returned by the server', async () => {
    invitations.list.mockResolvedValueOnce({ data: { invitations: [], next_cursor: 'opaque-next' } }).mockResolvedValueOnce({ data: { invitations: [{ id: '123e4567-e89b-12d3-a456-426614174000', version: 1, email: 'bea@example.test', audience: 'INTERNAL', status: 'ISSUED', expires_at: '2026-10-24T12:00:00Z' }], next_cursor: null } })
    show()
    fireEvent.click(await screen.findByRole('button', { name: 'Gestionar invitaciones' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Cargar más invitaciones' }))
    await waitFor(() => expect(invitations.list).toHaveBeenLastCalledWith(12, { limit: 25, cursor: 'opaque-next' }))
    expect(await screen.findByText('bea@example.test')).toBeInTheDocument()
  })

  it('issues with a UUID and refetches the authoritative first page', async () => {
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Gestionar invitaciones' }))
    fireEvent.change(screen.getByLabelText('Correo electrónico'), { target: { value: 'ana@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar invitación' }))
    await waitFor(() => expect(invitations.issue).toHaveBeenCalledWith(12, { email: 'ana@example.test', audience: 'INTERNAL' }, 'fixed-uuid'))
    await waitFor(() => expect(invitations.list).toHaveBeenCalledTimes(2))
    expect(invitations.list).toHaveBeenLastCalledWith(12, { limit: 25, cursor: null })
  })

  it('requires confirmation, sends the exact displayed id and version, then refetches', async () => {
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Gestionar invitaciones' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Revocar invitación para ana@example.test' }))
    const dialog = screen.getByRole('dialog', { name: 'Revocar invitación' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Revocar invitación' }))
    await waitFor(() => expect(invitations.revoke).toHaveBeenCalledWith(12, '550e8400-e29b-41d4-a716-446655440000', 3, 'fixed-uuid'))
    await waitFor(() => expect(invitations.list).toHaveBeenCalledTimes(2))
  })

  it('does not resend an unconfirmed email and prepares a new invitation only after revocation', async () => {
    invitations.list.mockResolvedValue({ data: { invitations: [{ ...firstPage.data.invitations[0], delivery_state: 'UNKNOWN', delivery_result_code: 'SMTP_UNKNOWN' }], next_cursor: null } })
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Gestionar invitaciones' }))
    expect(await screen.findByText(/No se pudo confirmar la entrega/)).toBeInTheDocument()
    expect(screen.getByText('El servidor de correo no confirmó la entrega. Revocá esta invitación antes de enviar una nueva.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Revocar invitación para ana@example.test' }))
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Revocar invitación' })).getByRole('button', { name: 'Revocar invitación' }))
    await waitFor(() => expect(invitations.revoke).toHaveBeenCalledTimes(1))
    expect(invitations.issue).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Correo electrónico')).toHaveValue('ana@example.test')
    expect(await screen.findByRole('alert')).toHaveTextContent('La invitación sin confirmación fue revocada.')
  })

  it('closes and revalidates authority after a forbidden list response', async () => {
    const onForbidden = vi.fn().mockResolvedValue(undefined)
    invitations.list.mockRejectedValue({ response: { status: 403 } })
    show({ onForbidden })
    await waitFor(() => expect(onForbidden).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('button', { name: 'Gestionar invitaciones' })).toBeInTheDocument()
  })

  it.each([[409, 'La invitación cambió antes de completar la operación. Se actualizó la lista.'], [503, 'El servicio de invitaciones no está disponible. Se actualizó la lista.']])('does not change local state on %i and refetches', async (status, message) => {
    invitations.issue.mockRejectedValueOnce({ response: { status } })
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Gestionar invitaciones' }))
    fireEvent.change(screen.getByLabelText('Correo electrónico'), { target: { value: 'ana@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar invitación' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    await waitFor(() => expect(invitations.list).toHaveBeenCalledTimes(2))
    expect(screen.getByText('ana@example.test')).toBeInTheDocument()
  })

  it('makes the parent dialog inert and Escape closes only the top revoke confirmation', async () => {
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Gestionar invitaciones' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Revocar invitación para ana@example.test' }))
    const manager = screen.getByRole('dialog', { name: 'Gestionar invitaciones' })
    expect(manager.querySelector('[data-office-invitation-content]')).toHaveAttribute('inert')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'Revocar invitación' })).not.toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Gestionar invitaciones' })).toBeInTheDocument()
  })
})
