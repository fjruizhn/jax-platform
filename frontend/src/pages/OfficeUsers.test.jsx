import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { MemoryRouter } from 'react-router-dom'
import { I18nProvider } from '../i18n/index.jsx'

const projects = vi.hoisted(() => ({ list: vi.fn(), detail: vi.fn(), create: vi.fn() }))
const invitations = vi.hoisted(() => ({ list: vi.fn(), issue: vi.fn(), revoke: vi.fn() }))
const members = vi.hoisted(() => ({ list: vi.fn(), grant: vi.fn(), changeRole: vi.fn(), revoke: vi.fn() }))
const people = vi.hoisted(() => ({ list: vi.fn(), projects: vi.fn(), assignableProjects: vi.fn() }))
const accountInvitations = vi.hoisted(() => ({ list: vi.fn(), issue: vi.fn(), revoke: vi.fn() }))
vi.mock('../api/virtualOffice', () => ({
  officeProjectsApi: projects, officeInvitationsApi: invitations,
  officeMembersApi: members, officePeopleApi: people, officeAccountInvitationsApi: accountInvitations,
}))

import OfficeUsers from './OfficeUsers'

const session = (role) => ({
  identity: { user_id: 1, tenant_id: 1, account_class: 'INTERNAL', global_role: role },
  capabilities: { create_project: role === 'OFFICE_SUPERUSER', administer_office: false, browse_people: role === 'GENERAL_ADMIN' },
})

function show(role) {
  return render(<I18nProvider><MemoryRouter><OfficeUsers officeSession={session(role)} onOfficeDenied={vi.fn()} /></MemoryRouter></I18nProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  projects.list.mockResolvedValue({ data: { projects: [{ id: 25, name: 'Estudio Kéré', description: null, tenant_id: 1 }], next_after_id: null } })
  projects.detail.mockResolvedValue({ data: { id: 25, name: 'Estudio Kéré', description: null, tenant_id: 1, scope_version: 1, audience: 'INTERNAL', capabilities: { manage_invitations: true } } })
  invitations.list.mockResolvedValue({ data: { invitations: [], next_cursor: null } })
  invitations.issue.mockResolvedValue({ data: { id: '00000000-0000-4000-8000-000000000001' } })
  members.list.mockResolvedValue({ data: { project_id: 25, members: [], next_cursor: null } })
  people.list.mockResolvedValue({ data: { people: [{ user_id: 1, tenant_id: 1, email: 'fernando@example.test', account_class: 'INTERNAL', global_role: 'OFFICE_SUPERUSER', status: 'ACTIVE', version: 1 }], next_after_user_id: null } })
  people.projects.mockResolvedValue({ data: { user_id: 1, projects: [], next_after_project_id: null } })
  people.assignableProjects.mockResolvedValue({ data: { projects: [{ id: 25, name: 'Estudio Kéré', tenant_id: 1 }], next_after_id: null } })
  accountInvitations.issue.mockResolvedValue({ data: { invitation_id: '00000000-0000-4000-8000-000000000002' } })
  accountInvitations.list.mockResolvedValue({ data: { invitations: [], next_after_id: null } })
})

describe('Office user administration', () => {
  it('shows active human Office accounts before any project exists', async () => {
    projects.list.mockResolvedValue({ data: { projects: [], next_after_id: null } })
    show('OFFICE_SUPERUSER')
    expect(await screen.findByRole('heading', { name: 'Usuarios de Oficina' })).toBeInTheDocument()
    expect(await screen.findByText('fernando@example.test')).toBeInTheDocument()
    expect(people.list).toHaveBeenCalledWith({ limit: 50, after_user_id: null })
    expect(screen.getByText('Creá un proyecto para empezar a invitar personas.')).toBeInTheDocument()
  })

  it('lets a project administrator invite a collaborator without global directory access or MFA', async () => {
    show('COLLABORATOR')
    expect(screen.queryByRole('button', { name: 'Crear proyecto' })).not.toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: 'Estudio Kéré' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Gestionar invitaciones' }))
    fireEvent.change(await screen.findByLabelText('Correo electrónico'), { target: { value: 'persona@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar invitación' }))
    await waitFor(() => expect(invitations.issue).toHaveBeenCalledWith(25, { email: 'persona@example.test', audience: 'INTERNAL' }, expect.any(String)))
    expect(people.list).not.toHaveBeenCalled()
  })

  it('offers first-project creation to the Office superuser without a global-admin MFA capability', async () => {
    projects.list.mockResolvedValue({ data: { projects: [], next_after_id: null } })
    show('OFFICE_SUPERUSER')
    expect(await screen.findByRole('button', { name: 'Crear proyecto' })).toBeInTheDocument()
    expect(screen.getByText('Creá un proyecto para empezar a invitar personas.')).toBeInTheDocument()
    expect(people.list).toHaveBeenCalledOnce()
  })

  it('shows a human account and lets the administrator assign its project', async () => {
    members.grant.mockResolvedValue({ data: { version: 1 } })
    show('OFFICE_SUPERUSER')
    fireEvent.click(await screen.findByRole('button', { name: /fernando@example.test/ }))
    expect(await screen.findByText('Todavía no participa en ningún proyecto.')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Agregar a proyecto'), { target: { value: '25' } })
    fireEvent.click(screen.getByRole('button', { name: 'Agregar' }))
    await waitFor(() => expect(members.grant).toHaveBeenCalledWith(25, 1, { grant_role: 'COLLABORATOR', audience: 'INTERNAL', expected_version: 0 }, expect.any(String)))
  })

  it('does not offer another tenant’s project for a person', async () => {
    projects.list.mockResolvedValue({ data: { projects: [{ id: 25, name: 'Estudio Kéré', tenant_id: 1 }, { id: 26, name: 'Otro tenant', tenant_id: 2 }], next_after_id: null } })
    show('OFFICE_SUPERUSER')
    fireEvent.click(await screen.findByRole('button', { name: /fernando@example.test/ }))
    const select = await screen.findByLabelText('Agregar a proyecto')
    expect(select).toHaveTextContent('Estudio Kéré')
    expect(select).not.toHaveTextContent('Otro tenant')
  })

  it('invites a person before assigning any project', async () => {
    projects.list.mockResolvedValue({ data: { projects: [], next_after_id: null } })
    show('OFFICE_SUPERUSER')
    fireEvent.change(screen.getByLabelText('Correo electrónico'), { target: { value: 'nueva@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar invitación' }))
    await waitFor(() => expect(accountInvitations.issue).toHaveBeenCalledWith('nueva@example.test', expect.any(String)))
    expect(members.grant).not.toHaveBeenCalled()
  })

  it('shows delivery status and requires confirmation before revoking a human invitation', async () => {
    const pending = { invitation_id: '00000000-0000-4000-8000-000000000003', email: 'pendiente@example.test', status: 'ISSUED', version: 1, delivery_state: 'UNKNOWN' }
    accountInvitations.list.mockResolvedValueOnce({ data: { invitations: [pending], next_after_id: null } })
      .mockResolvedValue({ data: { invitations: [], next_after_id: null } })
    accountInvitations.revoke.mockResolvedValue({ data: { version: 2 } })
    show('OFFICE_SUPERUSER')
    expect(await screen.findByText('pendiente@example.test')).toBeInTheDocument()
    expect(screen.getByText(/Entrega sin confirmar/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Revocar invitación' }))
    expect(accountInvitations.revoke).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('dialog').querySelector('button:last-child'))
    await waitFor(() => expect(accountInvitations.revoke).toHaveBeenCalledWith(pending.invitation_id, 1, expect.any(String)))
  })

  it('lets a delegated administrator select a tenant project without MFA', async () => {
    show('GENERAL_ADMIN')
    expect(await screen.findByText('fernando@example.test')).toBeInTheDocument()
    expect(people.assignableProjects).toHaveBeenCalledWith({ limit: 25, after_project_id: 0 })
    fireEvent.click(await screen.findByRole('button', { name: 'Estudio Kéré' }))
    expect(await screen.findByRole('button', { name: 'Gestionar invitaciones' })).toBeInTheDocument()
    expect(projects.detail).not.toHaveBeenCalled()
  })

  it('removes a person from a project only after confirmation', async () => {
    people.projects.mockResolvedValueOnce({ data: { user_id: 1, projects: [{ project_id: 25, name: 'Estudio Kéré', grant_role: 'COLLABORATOR', version: 3 }], next_after_project_id: null } })
      .mockResolvedValue({ data: { user_id: 1, projects: [], next_after_project_id: null } })
    members.revoke.mockResolvedValue({ data: { version: 4 } })
    show('OFFICE_SUPERUSER')
    fireEvent.click(await screen.findByRole('button', { name: /fernando@example.test/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Quitar' }))
    expect(members.revoke).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('dialog').querySelector('button:last-child'))
    await waitFor(() => expect(members.revoke).toHaveBeenCalledWith(25, 1, 3, expect.any(String)))
  })

  it('keeps the invitation and project visible when Escape is pressed during a pending send', async () => {
    invitations.issue.mockReturnValue(new Promise(() => {}))
    show('COLLABORATOR')
    fireEvent.click(await screen.findByRole('button', { name: 'Estudio Kéré' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Gestionar invitaciones' }))
    fireEvent.change(await screen.findByLabelText('Correo electrónico'), { target: { value: 'persona@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar invitación' }))
    await waitFor(() => expect(invitations.issue).toHaveBeenCalledTimes(1))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByRole('dialog', { name: 'Gestionar invitaciones' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Estudio Kéré' })).toBeInTheDocument()
  })

  it('lets the root promote an accepted project member without enabling the global directory', async () => {
    members.list.mockResolvedValue({ data: { project_id: 25, members: [{ user_id: 19, email: 'nueva@example.test', grant_role: 'COLLABORATOR', audience: 'INTERNAL', status: 'ACTIVE', version: 1 }], next_cursor: null } })
    members.changeRole.mockResolvedValue({ data: { version: 2 } })
    show('OFFICE_SUPERUSER')
    fireEvent.click(await screen.findByRole('button', { name: 'Estudio Kéré' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Gestionar personas del proyecto' }))
    fireEvent.change(await screen.findByLabelText('Papel de nueva@example.test'), { target: { value: 'PROJECT_ADMIN' } })
    await waitFor(() => expect(members.changeRole).toHaveBeenCalledWith(25, 19, { grant_role: 'PROJECT_ADMIN', expected_version: 1 }, expect.any(String)))
    expect(people.list).toHaveBeenCalledOnce()
  })
})
