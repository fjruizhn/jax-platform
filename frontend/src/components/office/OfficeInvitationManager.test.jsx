import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

  it('retries the same failed invitation intent with its original idempotency key', async () => {
    invitations.issue.mockRejectedValueOnce(new Error('lost')).mockResolvedValueOnce({ data: {} })
    vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValueOnce('key-a').mockReturnValueOnce('key-b') })
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Gestionar invitaciones' }))
    fireEvent.change(screen.getByLabelText('Correo electrónico'), { target: { value: 'ana@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar invitación' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('No se pudo completar la operación de invitación.')
    fireEvent.click(screen.getByRole('button', { name: 'Enviar invitación' }))
    await waitFor(() => expect(invitations.issue).toHaveBeenNthCalledWith(2, 12, { email: 'ana@example.test', audience: 'INTERNAL' }, 'key-a'))
  })

  it('uses a new idempotency key when a failed invitation payload changes', async () => {
    invitations.issue.mockRejectedValueOnce(new Error('lost')).mockResolvedValueOnce({ data: {} })
    vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValueOnce('key-a').mockReturnValueOnce('key-b') })
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Gestionar invitaciones' }))
    fireEvent.change(screen.getByLabelText('Correo electrónico'), { target: { value: 'ana@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar invitación' }))
    await screen.findByRole('alert')
    fireEvent.change(screen.getByLabelText('Correo electrónico'), { target: { value: 'bea@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar invitación' }))
    await waitFor(() => expect(invitations.issue).toHaveBeenNthCalledWith(2, 12, { email: 'bea@example.test', audience: 'INTERNAL' }, 'key-b'))
  })

  it('does not allow the invitation payload to be edited while it is in flight', async () => {
    let resolve
    invitations.issue.mockReturnValue(new Promise((done) => { resolve = done }))
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Gestionar invitaciones' }))
    fireEvent.change(screen.getByLabelText('Correo electrónico'), { target: { value: 'ana@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar invitación' }))
    expect(screen.getByLabelText('Correo electrónico')).toBeDisabled()
    expect(screen.getByLabelText('Audiencia')).toBeDisabled()
    resolve({ data: {} })
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

  it('makes the parent dialog inert and Escape closes only the top revoke confirmation', () => {
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Gestionar invitaciones' }))
    fireEvent.click(screen.getByRole('button', { name: 'Revocar invitación para ana@example.test' }))
    const manager = screen.getByRole('dialog', { name: 'Gestionar invitaciones' })
    expect(manager.querySelector('[data-office-invitation-content]')).toHaveAttribute('inert')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'Revocar invitación' })).not.toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Gestionar invitaciones' })).toBeInTheDocument()
  })

  it('reports a refresh failure separately after a committed invitation mutation', async () => {
    const onChanged = vi.fn().mockRejectedValue(new Error('refresh failed'))
    show({ onChanged })
    fireEvent.click(screen.getByRole('button', { name: 'Gestionar invitaciones' }))
    fireEvent.change(screen.getByLabelText('Correo electrónico'), { target: { value: 'ana@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar invitación' }))
    expect(await screen.findByRole('status')).toHaveTextContent('La invitación se guardó, pero no se pudo actualizar la lista.')
    expect(screen.queryByText('No se pudo completar la operación de invitación.')).not.toBeInTheDocument()
  })

  it('renders a revoke mutation failure inside the still-active top confirmation', async () => {
    invitations.revoke.mockRejectedValue(new Error('rejected'))
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Gestionar invitaciones' }))
    fireEvent.click(screen.getByRole('button', { name: 'Revocar invitación para ana@example.test' }))
    const confirm = screen.getByRole('dialog', { name: 'Revocar invitación' })
    fireEvent.click(within(confirm).getByRole('button', { name: 'Revocar invitación' }))
    expect(await within(confirm).findByRole('alert')).toHaveTextContent('No se pudo completar la operación de invitación.')
    expect(screen.getByRole('dialog', { name: 'Gestionar invitaciones' }).querySelector('[data-office-invitation-content]')).toHaveAttribute('inert')
  })
})
