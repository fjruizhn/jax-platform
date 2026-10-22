import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { MemoryRouter } from 'react-router-dom'
import { I18nProvider } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
const projects = vi.hoisted(() => ({ list: vi.fn(), detail: vi.fn() }))
vi.mock('../api/virtualOffice', () => ({ officeProjectsApi: projects }))
import VirtualOffice from './VirtualOffice'

function show() {
  return render(<I18nProvider><MemoryRouter><VirtualOffice officeSession={{ capabilities: {} }} onOfficeDenied={vi.fn()} /></MemoryRouter></I18nProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  useJaxStore.setState({ token: 'office-token', user: {
    user_id: 7, email: 'persona@example.test', account_class: 'OFFICE_INTERNAL',
  } })
})

describe('Virtual Office', () => {
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
})
