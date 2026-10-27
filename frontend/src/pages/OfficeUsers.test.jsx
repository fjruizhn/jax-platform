import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { MemoryRouter } from 'react-router-dom'
import { I18nProvider } from '../i18n/index.jsx'

const projects = vi.hoisted(() => ({ list: vi.fn(), detail: vi.fn(), create: vi.fn() }))
const invitations = vi.hoisted(() => ({ list: vi.fn(), issue: vi.fn(), revoke: vi.fn() }))
const members = vi.hoisted(() => ({ list: vi.fn(), grant: vi.fn(), changeRole: vi.fn(), revoke: vi.fn() }))
const people = vi.hoisted(() => ({ list: vi.fn() }))
vi.mock('../api/virtualOffice', () => ({
  officeProjectsApi: projects, officeInvitationsApi: invitations,
  officeMembersApi: members, officePeopleApi: people,
}))

import OfficeUsers from './OfficeUsers'

const session = (role) => ({
  identity: { user_id: 1, tenant_id: 1, account_class: 'INTERNAL', global_role: role },
  capabilities: { create_project: role === 'OFFICE_SUPERUSER', administer_office: false },
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
})

describe('Office user administration', () => {
  it('lets a project administrator invite a collaborator without global directory access or MFA', async () => {
    show('COLLABORATOR')
    expect(screen.queryByRole('button', { name: 'Crear proyecto' })).not.toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: 'Estudio Kéré' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Gestionar invitaciones' }))
    fireEvent.change(screen.getByLabelText('Correo electrónico'), { target: { value: 'persona@example.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Enviar invitación' }))
    await waitFor(() => expect(invitations.issue).toHaveBeenCalledWith(25, { email: 'persona@example.test', audience: 'INTERNAL' }, expect.any(String)))
    expect(people.list).not.toHaveBeenCalled()
  })

  it('offers first-project creation to the Office superuser without a global-admin MFA capability', async () => {
    projects.list.mockResolvedValue({ data: { projects: [], next_after_id: null } })
    show('OFFICE_SUPERUSER')
    expect(await screen.findByRole('button', { name: 'Crear proyecto' })).toBeInTheDocument()
    expect(screen.getByText('Creá un proyecto para empezar a invitar personas.')).toBeInTheDocument()
    expect(people.list).not.toHaveBeenCalled()
  })

  it('keeps the invitation and project visible when Escape is pressed during a pending send', async () => {
    invitations.issue.mockReturnValue(new Promise(() => {}))
    show('COLLABORATOR')
    fireEvent.click(await screen.findByRole('button', { name: 'Estudio Kéré' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Gestionar invitaciones' }))
    fireEvent.change(screen.getByLabelText('Correo electrónico'), { target: { value: 'persona@example.test' } })
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
    expect(people.list).not.toHaveBeenCalled()
  })
})
