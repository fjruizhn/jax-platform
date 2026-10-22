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
