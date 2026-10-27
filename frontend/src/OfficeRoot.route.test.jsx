// OP 358 pieza 1: en la instalación de la Oficina Virtual (build servido en
// su dominio, VITE_OFFICE_HOSTS) la raíz del dominio recibe el login de la
// oficina y la oficina es la única cara — no la Mesa de Axioma. Aquí el modo
// instalación se mockea; su lectura real de VITE_OFFICE_HOSTS se prueba en
// lib/instalacionOficina.test.js.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { I18nProvider } from './i18n/index.jsx'
import { useJaxStore } from './store/useJaxStore'

vi.mock('./apariencia/sincronizarApariencia', () => ({ sincronizarApariencia: vi.fn(() => Promise.resolve()) }))
const instalacionOficina = vi.hoisted(() => vi.fn(() => true))
vi.mock('./lib/instalacionOficina', () => ({ esInstalacionDeOficina: instalacionOficina }))
const session = vi.hoisted(() => vi.fn())
const bootstrap = vi.hoisted(() => vi.fn())
const projects = vi.hoisted(() => ({ list: vi.fn(), detail: vi.fn() }))
vi.mock('./api/client', () => ({ default: { get: vi.fn(() => new Promise(() => {})), post: vi.fn(() => new Promise(() => {})) } }))
vi.mock('./api/virtualOffice', () => ({
  mfaApi: { status: vi.fn(() => new Promise(() => {})) },
  officeSessionApi: { read: session, bootstrap },
  officeProjectsApi: projects,
  officeVerticalesApi: { list: vi.fn(async () => ({ data: { verticales: [] } })) },
}))

import App from './App.jsx'

const conSesionDeOficina = () => useJaxStore.setState({ token: 'office-token', user: { user_id: 8, email: 'client@example.test', account_class: 'INTERNAL' }, sessionRestoring: false, _sessionEpoch: 1 })
const sinSesion = () => useJaxStore.setState({ token: null, user: null, sessionRestoring: false, _sessionEpoch: 1, mfaRecoveryCodes: null, mfaRecoveryCodesOwnerId: null })
const allowed = () => ({ data: {
  identity: { user_id: 8, tenant_id: 4, account_class: 'INTERNAL', global_role: 'COLLABORATOR' },
  mfa: { verified: true, recent: true }, capabilities: { create_project: false, administer_office: false, administer_identity_security: false },
} })

describe('Raíz = login de la oficina (instalación de la Oficina Virtual)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    instalacionOficina.mockReturnValue(true)
    projects.list.mockResolvedValue({ data: { projects: [], next_after_id: null } })
    HTMLElement.prototype.scrollIntoView = vi.fn()
    window.history.pushState({}, '', '/')
  })

  it('la raíz sin sesión recibe el login de la oficina', async () => {
    sinSesion()
    render(<I18nProvider><App /></I18nProvider>)
    await waitFor(() => expect(window.location.pathname).toBe('/login'))
  })

  // OP 358 pieza 2: en esta instalación el login viste la piel de los
  // studios — la marca de la oficina (MarcaOficina) en lugar del ojo de HAL,
  // y el intro de la oficina como tagline.
  it('el login de la oficina viste la marca de la oficina, no el ojo de HAL', async () => {
    sinSesion()
    window.history.pushState({}, '', '/login')
    render(<I18nProvider><App /></I18nProvider>)
    expect(await screen.findByRole('img', { name: 'Oficina virtual' })).toBeInTheDocument()
    expect(screen.queryByLabelText(/Ojo HAL/)).not.toBeInTheDocument()
    expect(screen.getByText('Espacio protegido para proyectos, personas y permisos de Oficina.')).toBeInTheDocument()
  })

  it('la raíz con sesión de oficina entra directo a la oficina, no a la Mesa de Axioma', async () => {
    conSesionDeOficina()
    session.mockResolvedValue(allowed())
    render(<I18nProvider><App /></I18nProvider>)
    expect(await screen.findByRole('heading', { name: 'Oficina virtual' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/virtual-office')
  })

  it('las rutas de la Mesa de Axioma no existen en la instalación de la oficina', async () => {
    conSesionDeOficina()
    session.mockResolvedValue(allowed())
    window.history.pushState({}, '', '/admin/users')
    render(<I18nProvider><App /></I18nProvider>)
    expect(await screen.findByRole('heading', { name: 'Oficina virtual' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/virtual-office')
  })

  it('un login exitoso termina en la oficina, también para una cuenta interna clásica', async () => {
    sinSesion()
    window.history.pushState({}, '', '/login')
    // El login del store real hace api.post; aquí se sobreescribe la acción
    // para establecer la sesión igual que establecerSesion y devolver la data
    // que destination() recibe.
    useJaxStore.setState({
      login: vi.fn(async (email) => {
        useJaxStore.setState({ token: 'office-token', user: { user_id: 8, email, account_class: 'INTERNAL' }, sessionRestoring: false })
        return { access_token: 'office-token', user_id: 8, account_class: 'INTERNAL' }
      }),
    })
    session.mockResolvedValue(allowed())
    render(<I18nProvider><App /></I18nProvider>)
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'client@example.test' } })
    fireEvent.change(screen.getByLabelText('Contraseña'), { target: { value: 'password-de-prueba' } })
    fireEvent.click(screen.getByRole('button', { name: 'Entrar a Oficina virtual' }))
    expect(await screen.findByRole('heading', { name: 'Oficina virtual' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/virtual-office')
  })

  it('una URL desconocida sin sesión acaba en el login de la oficina', async () => {
    sinSesion()
    window.history.pushState({}, '', '/no-existe')
    render(<I18nProvider><App /></I18nProvider>)
    await waitFor(() => expect(window.location.pathname).toBe('/login'))
  })
})

describe('Instalación de Axioma (sin hosts de oficina): comportamiento intacto', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    instalacionOficina.mockReturnValue(false)
    HTMLElement.prototype.scrollIntoView = vi.fn()
  })

  it('una URL desconocida sin sesión sigue acabando en el login de siempre', async () => {
    sinSesion()
    window.history.pushState({}, '', '/no-existe')
    render(<I18nProvider><App /></I18nProvider>)
    await waitFor(() => expect(window.location.pathname).toBe('/login'))
    // Pieza 2: sin instalación de oficina no hay piel de estudio — el ojo de
    // HAL sigue siendo la cara del login de la Mesa.
    expect(await screen.findByLabelText(/Ojo HAL/)).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: 'Axioma' })).not.toBeInTheDocument()
  })
})
