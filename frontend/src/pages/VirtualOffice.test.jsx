import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { MemoryRouter } from 'react-router-dom'
import { I18nProvider } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
const projects = vi.hoisted(() => ({ list: vi.fn(), detail: vi.fn(), create: vi.fn() }))
const invitations = vi.hoisted(() => ({ list: vi.fn(), issue: vi.fn(), revoke: vi.fn() }))
const people = vi.hoisted(() => ({ list: vi.fn() }))
const members = vi.hoisted(() => ({ list: vi.fn(), grant: vi.fn(), changeRole: vi.fn(), revoke: vi.fn() }))
const agents = vi.hoisted(() => ({ list: vi.fn(), engines: vi.fn(), create: vi.fn(), bind: vi.fn(), run: vi.fn() }))
const verticales = vi.hoisted(() => ({ list: vi.fn() }))
vi.mock('../api/virtualOffice', () => ({ officeProjectsApi: projects, officeInvitationsApi: invitations, officePeopleApi: people, officeMembersApi: members, officeAgentsApi: agents, officeVerticalesApi: verticales, officeSessionApi: { read: vi.fn() } }))
import VirtualOffice from './VirtualOffice'

function show() {
  return render(<I18nProvider><MemoryRouter><VirtualOffice officeSession={{ capabilities: {} }} onOfficeDenied={vi.fn()} /></MemoryRouter></I18nProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  invitations.list.mockResolvedValue({ data: { invitations: [], next_cursor: null } })
  members.list.mockResolvedValue({ data: { project_id: 25, members: [], next_cursor: null } })
  agents.list.mockResolvedValue({ data: { items: [] } })
  agents.engines.mockResolvedValue({ data: { items: [] } })
  verticales.list.mockResolvedValue({ data: { verticales: [] } })
  useJaxStore.setState({ token: 'office-token', user: {
    user_id: 7, email: 'persona@example.test', account_class: 'OFFICE_INTERNAL',
  } })
})

describe('Virtual Office', () => {
  it('lets a capable Office user create the first project', async () => {
    projects.list.mockResolvedValue({ data: { projects: [], next_after_id: null } })
    projects.create.mockResolvedValue({ data: { id: 31, created: true } })
    projects.detail.mockResolvedValue({ data: { id: 31, name: 'Nuevo proyecto', description: null, tenant_id: 4, scope_version: 1, audience: 'INTERNAL' } })
    render(<I18nProvider><MemoryRouter><VirtualOffice officeSession={{ identity: { tenant_id: 4 }, capabilities: { create_project: true } }} onOfficeDenied={vi.fn()} /></MemoryRouter></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Crear proyecto' }))
    fireEvent.change(screen.getByLabelText('Nombre del proyecto'), { target: { value: 'Nuevo proyecto' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Crear proyecto' })[1])
    await waitFor(() => expect(projects.create).toHaveBeenCalledWith({ tenant_id: 4, name: 'Nuevo proyecto', description: null }, expect.any(String)))
  })

  it('retries a failed project creation after remount with the saved intent key', async () => {
    projects.list.mockResolvedValue({ data: { projects: [], next_after_id: null } })
    projects.create.mockRejectedValueOnce({ response: { status: 503 } }).mockResolvedValueOnce({ data: { id: 31, created: false } })
    projects.detail.mockResolvedValue({ data: { id: 31, name: 'Nuevo proyecto', description: null, tenant_id: 4, scope_version: 1, audience: 'INTERNAL' } })
    const officeSession = { identity: { user_id: 7, tenant_id: 4 }, capabilities: { create_project: true } }
    const first = render(<I18nProvider><MemoryRouter><VirtualOffice officeSession={officeSession} onOfficeDenied={vi.fn()} /></MemoryRouter></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Crear proyecto' }))
    fireEvent.change(screen.getByLabelText('Nombre del proyecto'), { target: { value: 'Nuevo proyecto' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Crear proyecto' })[1])
    await waitFor(() => expect(projects.create).toHaveBeenCalledTimes(1))
    const firstKey = projects.create.mock.calls[0][1]
    first.unmount()

    render(<I18nProvider><MemoryRouter><VirtualOffice officeSession={officeSession} onOfficeDenied={vi.fn()} /></MemoryRouter></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Crear proyecto' }))
    fireEvent.change(screen.getByLabelText('Nombre del proyecto'), { target: { value: 'Nuevo proyecto' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Crear proyecto' })[1])
    await waitFor(() => expect(projects.create).toHaveBeenCalledTimes(2))
    expect(projects.create.mock.calls[1][1]).toBe(firstKey)
  })

  it('lets an Office administrator add an existing person to a project', async () => {
    projects.list.mockResolvedValue({ data: { projects: [{ id: 25, name: 'Equipo', description: '', tenant_id: 4 }], next_after_id: null } })
    projects.detail.mockResolvedValue({ data: { id: 25, name: 'Equipo', description: '', tenant_id: 4, scope_version: 1, audience: 'INTERNAL', capabilities: { manage_invitations: true } } })
    people.list.mockResolvedValue({ data: { people: [{ user_id: 9, email: 'equipo@example.test' }], next_after_user_id: null } })
    members.grant.mockResolvedValue({ data: { user_id: 9 } })
    render(<I18nProvider><MemoryRouter><VirtualOffice officeSession={{ identity: { global_role: 'GENERAL_ADMIN' }, capabilities: { administer_office: true } }} onOfficeDenied={vi.fn()} /></MemoryRouter></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Equipo' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Agregar persona existente' }))
    fireEvent.change(await screen.findByLabelText('Persona'), { target: { value: '9' } })
    expect(screen.getByText('La persona se agregará como colaboradora.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Papel en el proyecto')).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Administrador del proyecto' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Agregar persona' }))
    await waitFor(() => expect(members.grant).toHaveBeenCalledWith(25, 9, { grant_role: 'COLLABORATOR', audience: 'INTERNAL', expected_version: 0 }, expect.any(String)))
  })

  it('shows member management to a project administrator from the project capability, without offering role elevation', async () => {
    projects.list.mockResolvedValue({ data: { projects: [{ id: 25, name: 'Equipo', description: '', tenant_id: 4 }], next_after_id: null } })
    projects.detail.mockResolvedValue({ data: { id: 25, name: 'Equipo', description: '', tenant_id: 4, scope_version: 1, audience: 'INTERNAL', capabilities: { manage_invitations: true } } })
    members.list.mockResolvedValue({ data: { project_id: 25, members: [{ user_id: 19, email: 'proyecto@example.test', grant_role: 'COLLABORATOR', audience: 'INTERNAL', status: 'ACTIVE', version: 1 }], next_cursor: null } })
    const onOfficeDenied = vi.fn()
    render(<I18nProvider><MemoryRouter><VirtualOffice officeSession={{ identity: { global_role: 'COLLABORATOR' }, capabilities: { administer_office: false } }} onOfficeDenied={onOfficeDenied} /></MemoryRouter></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Equipo' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Gestionar personas del proyecto' }))
    expect(await screen.findByRole('dialog', { name: 'Personas del proyecto' })).toBeInTheDocument()
    await waitFor(() => expect(members.list).toHaveBeenCalledWith(25, { limit: 50, cursor: 0 }))
    expect(people.list).not.toHaveBeenCalled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(onOfficeDenied).not.toHaveBeenCalled()
    expect(screen.getByText('Para agregar personas, usá Gestionar invitaciones.')).toBeInTheDocument()
    expect(screen.getByText('proyecto@example.test')).toBeInTheDocument()
    expect(screen.queryByText('Usuario #19')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Papel en el proyecto')).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Administrador del proyecto' })).not.toBeInTheDocument()
  })

  it('does not show member management from a global session flag when the project capability is absent', async () => {
    projects.list.mockResolvedValue({ data: { projects: [{ id: 25, name: 'Equipo', description: '', tenant_id: 4 }], next_after_id: null } })
    projects.detail.mockResolvedValue({ data: { id: 25, name: 'Equipo', description: '', tenant_id: 4, scope_version: 1, audience: 'INTERNAL', capabilities: { manage_invitations: false } } })
    render(<I18nProvider><MemoryRouter><VirtualOffice officeSession={{ identity: { global_role: 'GENERAL_ADMIN' }, capabilities: { administer_office: true } }} onOfficeDenied={vi.fn()} /></MemoryRouter></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Equipo' }))
    expect(await screen.findByRole('dialog', { name: 'Equipo' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Agregar persona existente' })).not.toBeInTheDocument()
  })

  it('offers the project-administrator role only to the Office superuser capability document', async () => {
    projects.list.mockResolvedValue({ data: { projects: [{ id: 25, name: 'Equipo', description: '', tenant_id: 4 }], next_after_id: null } })
    projects.detail.mockResolvedValue({ data: { id: 25, name: 'Equipo', description: '', tenant_id: 4, scope_version: 1, audience: 'INTERNAL', capabilities: { manage_invitations: true } } })
    people.list.mockResolvedValue({ data: { people: [{ user_id: 9, email: 'equipo@example.test' }], next_after_user_id: null } })
    render(<I18nProvider><MemoryRouter><VirtualOffice officeSession={{ identity: { global_role: 'OFFICE_SUPERUSER' }, capabilities: { administer_office: true } }} onOfficeDenied={vi.fn()} /></MemoryRouter></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Equipo' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Agregar persona existente' }))
    expect(await screen.findByLabelText('Papel en el proyecto')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Administrador del proyecto' })).toBeInTheDocument()
  })

  it('updates and revokes a listed member against the exact server version, including a person outside the directory page', async () => {
    projects.list.mockResolvedValue({ data: { projects: [{ id: 25, name: 'Equipo', description: '', tenant_id: 4 }], next_after_id: null } })
    projects.detail.mockResolvedValue({ data: { id: 25, name: 'Equipo', description: '', tenant_id: 4, scope_version: 1, audience: 'INTERNAL', capabilities: { manage_invitations: true } } })
    people.list.mockResolvedValue({ data: { people: [], next_after_user_id: null } })
    members.list.mockResolvedValue({ data: { project_id: 25, members: [{ user_id: 209, email: 'miembro@example.test', grant_role: 'COLLABORATOR', audience: 'INTERNAL', status: 'ACTIVE', version: 3 }], next_cursor: null } })
    members.changeRole.mockResolvedValue({ data: { version: 4 } })
    members.revoke.mockResolvedValue({ data: { version: 5 } })
    render(<I18nProvider><MemoryRouter><VirtualOffice officeSession={{ identity: { global_role: 'OFFICE_SUPERUSER' }, capabilities: { administer_office: true } }} onOfficeDenied={vi.fn()} /></MemoryRouter></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Equipo' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Agregar persona existente' }))
    expect(await screen.findByText('miembro@example.test')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Papel de miembro@example.test'), { target: { value: 'PROJECT_ADMIN' } })
    await waitFor(() => expect(members.changeRole).toHaveBeenCalledWith(25, 209, { grant_role: 'PROJECT_ADMIN', expected_version: 3 }, expect.any(String)))
    fireEvent.click(screen.getByRole('button', { name: 'Revocar acceso' }))
    await waitFor(() => expect(members.revoke).toHaveBeenCalledWith(25, 209, 4, expect.any(String)))
  })

  it('refreshes the server member list after a role-version conflict', async () => {
    projects.list.mockResolvedValue({ data: { projects: [{ id: 25, name: 'Equipo', description: '', tenant_id: 4 }], next_after_id: null } })
    projects.detail.mockResolvedValue({ data: { id: 25, name: 'Equipo', description: '', tenant_id: 4, scope_version: 1, audience: 'INTERNAL', capabilities: { manage_invitations: true } } })
    people.list.mockResolvedValue({ data: { people: [], next_after_user_id: null } })
    const current = { data: { project_id: 25, members: [{ user_id: 209, grant_role: 'COLLABORATOR', audience: 'INTERNAL', status: 'ACTIVE', version: 3 }], next_cursor: null } }
    const refreshed = { data: { project_id: 25, members: [{ user_id: 209, grant_role: 'PROJECT_ADMIN', audience: 'INTERNAL', status: 'ACTIVE', version: 4 }], next_cursor: null } }
    members.list.mockResolvedValueOnce(current).mockResolvedValueOnce(refreshed)
    members.changeRole.mockRejectedValue({ response: { status: 409 } })
    render(<I18nProvider><MemoryRouter><VirtualOffice officeSession={{ identity: { global_role: 'OFFICE_SUPERUSER' }, capabilities: { administer_office: true } }} onOfficeDenied={vi.fn()} /></MemoryRouter></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Equipo' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Agregar persona existente' }))
    fireEvent.change(await screen.findByLabelText('Papel de Usuario #209'), { target: { value: 'PROJECT_ADMIN' } })
    expect(await screen.findByRole('alert')).toHaveTextContent('La persona cambió antes de completar la operación.')
    await waitFor(() => expect(members.list).toHaveBeenCalledTimes(2))
    expect(screen.getByLabelText('Papel de Usuario #209')).toHaveValue('PROJECT_ADMIN')
  })

  it('keeps the member dialog open and explains a revoked administration capability', async () => {
    projects.list.mockResolvedValue({ data: { projects: [{ id: 25, name: 'Equipo', description: '', tenant_id: 4 }], next_after_id: null } })
    projects.detail.mockResolvedValue({ data: { id: 25, name: 'Equipo', description: '', tenant_id: 4, scope_version: 1, audience: 'INTERNAL', capabilities: { manage_invitations: true } } })
    people.list.mockResolvedValue({ data: { people: [{ user_id: 9, email: 'equipo@example.test' }], next_after_user_id: null } })
    members.grant.mockRejectedValue({ response: { status: 403 } })
    render(<I18nProvider><MemoryRouter><VirtualOffice officeSession={{ capabilities: { administer_office: true } }} onOfficeDenied={vi.fn()} /></MemoryRouter></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: 'Equipo' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Agregar persona existente' }))
    fireEvent.change(await screen.findByLabelText('Persona'), { target: { value: '9' } })
    fireEvent.click(screen.getByRole('button', { name: 'Agregar persona' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Tu acceso cambió y ya no podés administrar personas en este proyecto.')
    expect(screen.getByRole('dialog', { name: 'Agregar una persona existente' })).toBeInTheDocument()
  })
  it('shows only the server-filtered project fields and opens a read-only detail', async () => {
    projects.list.mockResolvedValue({ data: { projects: [{ id: 12, name: 'Casa Omoa', description: 'Entrega inicial', tenant_id: 4 }], next_after_id: null } })
    projects.detail.mockResolvedValue({ data: { id: 12, name: 'Casa Omoa', description: 'Entrega inicial', tenant_id: 4, scope_version: 7, audience: 'INTERNAL' } })
    show()
    expect(await screen.findByRole('heading', { name: 'Oficina virtual' })).toBeInTheDocument()
    expect(screen.getByText('persona@example.test')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Casa Omoa' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Casa Omoa' }))
    expect(await screen.findByRole('dialog', { name: 'Casa Omoa' })).toHaveTextContent('Entrega inicial')
    expect(screen.queryByText('INTERNAL')).not.toBeInTheDocument()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Otorgar permiso|Invitar persona/ })).not.toBeInTheDocument()
  })

  it('continues a server cursor without inventing project membership', async () => {
    projects.list.mockResolvedValueOnce({ data: { projects: [], next_after_id: 12 } }).mockResolvedValueOnce({ data: { projects: [{ id: 13, name: 'Siguiente', description: '', tenant_id: 4 }], next_after_id: null } })
    show()
    fireEvent.click(await screen.findByRole('button', { name: 'Cargar más' }))
    await waitFor(() => expect(projects.list).toHaveBeenLastCalledWith({ limit: 25, after_id: 12 }))
    expect(await screen.findByRole('button', { name: 'Siguiente' })).toBeInTheDocument()
  })

  it.each(['OFFICE_INTERNAL', 'EXTERNAL_CLIENT'])('accepts a null published description for %s in list and detail', async (account_class) => {
    useJaxStore.setState({ token: 'office-token', user: { user_id: 7, email: 'persona@example.test', account_class } })
    projects.list.mockResolvedValue({ data: { projects: [{ id: 21, name: 'Publicable', description: null, tenant_id: 4 }], next_after_id: null } })
    projects.detail.mockResolvedValue({ data: { id: 21, name: 'Publicable', description: null, tenant_id: 4, scope_version: 7, audience: 'CLIENT_PUBLISHED' } })
    show()
    fireEvent.click(await screen.findByRole('button', { name: 'Publicable' }))
    expect(await screen.findByRole('dialog', { name: 'Publicable' })).toHaveTextContent('Sin descripción.')
  })

  it('keeps a long unbroken detail scrollable and gives its close action a touch target', async () => {
    const long = 'x'.repeat(2000)
    projects.list.mockResolvedValue({ data: { projects: [{ id: 22, name: 'Largo', description: long, tenant_id: 4 }], next_after_id: null } })
    projects.detail.mockResolvedValue({ data: { id: 22, name: 'Largo', description: long, tenant_id: 4, scope_version: 7, audience: 'INTERNAL' } })
    show()
    fireEvent.click(await screen.findByRole('button', { name: 'Largo' }))
    const dialog = await screen.findByRole('dialog', { name: 'Largo' })
    expect(dialog.className).toMatch(/max-h-/)
    expect(screen.getByText(long).className).toMatch(/break-words/)
    expect(screen.getByRole('button', { name: 'Cerrar' }).className).toMatch(/min-h-11/)
  })

  it('wraps a 255-character unbroken project name in both list and detail title', async () => {
    const name = 'n'.repeat(255)
    projects.list.mockResolvedValue({ data: { projects: [{ id: 23, name, description: null, tenant_id: 4 }], next_after_id: null } })
    projects.detail.mockResolvedValue({ data: { id: 23, name, description: null, tenant_id: 4, scope_version: 7, audience: 'CLIENT_PUBLISHED' } })
    show()
    const project = await screen.findByRole('button', { name })
    expect(project.className).toMatch(/break-words/)
    fireEvent.click(project)
    const dialog = await screen.findByRole('dialog', { name })
    expect(dialog.querySelector('h2').className).toMatch(/break-words/)
  })

  it('does not issue duplicate cursor requests while the next page is loading', async () => {
    let resolveNext
    projects.list.mockResolvedValueOnce({ data: { projects: [], next_after_id: 30 } }).mockReturnValueOnce(new Promise((resolve) => { resolveNext = resolve }))
    show()
    const more = await screen.findByRole('button', { name: 'Cargar más' })
    fireEvent.click(more); fireEvent.click(more)
    expect(projects.list).toHaveBeenCalledTimes(2)
    expect(more).toBeDisabled()
    resolveNext({ data: { projects: [], next_after_id: null } })
  })

  it('shows translated empty and error states without retaining a prior project list', async () => {
    projects.list.mockResolvedValue({ data: { projects: [], next_after_id: null } })
    const { unmount } = show()
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('No hay proyectos disponibles.'))
    unmount()
    projects.list.mockRejectedValue({ response: { status: 503 } })
    show()
    expect(await screen.findByRole('alert')).toHaveTextContent('No se pudieron cargar los proyectos.')
    expect(screen.queryByRole('button', { name: 'Casa Omoa' })).not.toBeInTheDocument()
  })

  it('opens access information in the shared keyboard-accessible dialog', () => {
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Información de acceso' }))
    expect(screen.getByRole('dialog', { name: 'Acceso de Oficina virtual' })).toBeInTheDocument()
  })

  // OP 358 pieza 2: esta página es standalone también en la instalación de
  // Axioma — el botón de salida le hace falta en las dos. Fuera de la
  // instalación de oficina no hay piel de estudio: tarjetas de siempre, sin marca.
  it('offers the store logout and stays in the classic Axioma cards outside the office install', async () => {
    projects.list.mockResolvedValue({ data: { projects: [], next_after_id: null } })
    const logout = vi.fn()
    useJaxStore.setState({ logout })
    show()
    const salir = await screen.findByRole('button', { name: 'Salir' })
    expect(salir).toBeEnabled()
    expect(screen.queryByRole('img', { name: 'Oficina virtual' })).not.toBeInTheDocument()
    expect(document.querySelectorAll('.tarjeta-oficina')).toHaveLength(0)
    fireEvent.click(salir)
    expect(logout).toHaveBeenCalledTimes(1)
  })

  it('mounts invitation controls only for the boolean capability computed by the detail endpoint', async () => {
    projects.list.mockResolvedValue({ data: { projects: [{ id: 24, name: 'Administrable', description: '', tenant_id: 4 }], next_after_id: null } })
    projects.detail.mockResolvedValue({ data: { id: 24, name: 'Administrable', description: '', tenant_id: 4, scope_version: 7, audience: 'INTERNAL', capabilities: { manage_invitations: true } } })
    show()
    fireEvent.click(await screen.findByRole('button', { name: 'Administrable' }))
    expect(await screen.findByRole('button', { name: 'Gestionar invitaciones' })).toBeInTheDocument()
    expect(invitations.list).toHaveBeenCalledWith(24, { limit: 25, cursor: null })
  })
})
