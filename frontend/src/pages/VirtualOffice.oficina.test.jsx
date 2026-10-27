// OP 358 pieza 4: en la instalación de oficina el hub anuncia el registro
// del servidor y una sala activa filtra la lista de proyectos de su
// vertical. Este archivo fuerza esInstalacionDeOficina() antes de importar
// la página; el resto de la suite (VirtualOffice.test.jsx) corre en la
// instalación de Axioma, donde no hay hub.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { MemoryRouter } from 'react-router-dom'
import { I18nProvider } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
vi.mock('../lib/instalacionOficina', () => ({ esInstalacionDeOficina: () => true }))
const projects = vi.hoisted(() => ({ list: vi.fn(), detail: vi.fn(), create: vi.fn() }))
const invitations = vi.hoisted(() => ({ list: vi.fn(), issue: vi.fn(), revoke: vi.fn() }))
const people = vi.hoisted(() => ({ list: vi.fn() }))
const members = vi.hoisted(() => ({ list: vi.fn(), grant: vi.fn(), changeRole: vi.fn(), revoke: vi.fn() }))
const agents = vi.hoisted(() => ({ list: vi.fn(), engines: vi.fn(), create: vi.fn(), bind: vi.fn(), run: vi.fn() }))
const verticales = vi.hoisted(() => ({ list: vi.fn() }))
vi.mock('../api/virtualOffice', () => ({ officeProjectsApi: projects, officeInvitationsApi: invitations, officePeopleApi: people, officeMembersApi: members, officeAgentsApi: agents, officeVerticalesApi: verticales, officeSessionApi: { read: vi.fn() } }))
import VirtualOffice from './VirtualOffice'

const REGISTRO = [
  { id: 'junta', estado: 'proximamente' },
  { id: 'agencia', estado: 'activa' },
]

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
  projects.list.mockResolvedValue({ data: { projects: [], next_after_id: null } })
  verticales.list.mockResolvedValue({ data: { verticales: REGISTRO } })
  useJaxStore.setState({ token: 'office-token', user: {
    user_id: 7, email: 'persona@example.test', account_class: 'OFFICE_INTERNAL',
  } })
})

describe('Virtual Office · instalación de oficina', () => {
  it('una sala activa filtra la lista por su vertical y el filtro se quita', async () => {
    show()
    fireEvent.click(await screen.findByRole('button', { name: /Agencia · Susan Kare Studio/ }))
    await waitFor(() => expect(projects.list).toHaveBeenLastCalledWith({ limit: 25, after_id: null, vertical: 'agencia' }))
    expect(screen.getByText('Filtrado por sala:')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Quitar filtro' }))
    await waitFor(() => expect(projects.list).toHaveBeenLastCalledWith({ limit: 25, after_id: null }))
    expect(screen.queryByText('Filtrado por sala:')).not.toBeInTheDocument()
  })

  it('la sala filtrada sin proyectos muestra su vacío propio', async () => {
    show()
    fireEvent.click(await screen.findByRole('button', { name: /Agencia · Susan Kare Studio/ }))
    await screen.findByText('Esta sala todavía no tiene proyectos.')
    expect(projects.list).toHaveBeenLastCalledWith({ limit: 25, after_id: null, vertical: 'agencia' })
  })

  it('si el registro no carga, el hub muestra el error y la lista de proyectos sigue', async () => {
    verticales.list.mockRejectedValue({ response: { status: 503 } })
    show()
    const alerta = await screen.findByRole('alert')
    expect(alerta).toHaveTextContent('No se pudieron cargar las salas de la oficina.')
    await waitFor(() => expect(projects.list).toHaveBeenCalled())
  })
})
