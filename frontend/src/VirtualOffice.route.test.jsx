import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { I18nProvider } from './i18n/index.jsx'
import { useJaxStore } from './store/useJaxStore'

vi.mock('./apariencia/sincronizarApariencia', () => ({ sincronizarApariencia: vi.fn(() => Promise.resolve()) }))
const session = vi.hoisted(() => vi.fn())
const bootstrap = vi.hoisted(() => vi.fn())
const projects = vi.hoisted(() => ({ list: vi.fn(), detail: vi.fn(), create: vi.fn() }))
vi.mock('./api/client', () => ({ default: { get: vi.fn(() => new Promise(() => {})), post: vi.fn(() => new Promise(() => {})) } }))
vi.mock('./api/virtualOffice', () => ({
  mfaApi: { status: vi.fn(() => new Promise(() => {})) },
  officeSessionApi: { read: session, bootstrap },
  officeProjectsApi: projects,
  officeVerticalesApi: { list: vi.fn(async () => ({ data: { verticales: [] } })) },
}))

import App from './App.jsx'

describe('Virtual Office route', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    projects.list.mockResolvedValue({ data: { projects: [], next_after_id: null } })
    HTMLElement.prototype.scrollIntoView = vi.fn()
    useJaxStore.setState({ token: 'office-token', user: { user_id: 8, email: 'client@example.test', account_class: 'INTERNAL' }, sessionRestoring: false, _sessionEpoch: 1 })
    window.history.pushState({}, '', '/virtual-office')
  })

  const allowed = (overrides = {}) => ({ data: {
    identity: { user_id: 8, tenant_id: 4, account_class: 'INTERNAL', global_role: 'COLLABORATOR' },
    mfa: { verified: true, recent: true }, capabilities: { create_project: false, administer_office: false, administer_identity_security: false }, ...overrides,
  } })

  it('allows an INTERNAL Office root only after the server allows the session', async () => {
    session.mockResolvedValue(allowed())
    render(<I18nProvider><App /></I18nProvider>)
    expect(await screen.findByRole('heading', { name: 'Oficina virtual' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/virtual-office')
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Otorgar permiso|Invitar persona|Elegir proyecto/ })).not.toBeInTheDocument()
  })

  it('opens Office user administration after normal sign-in without MFA', async () => {
    session.mockResolvedValue(allowed({ identity: { user_id: 8, tenant_id: 4, account_class: 'INTERNAL', global_role: 'OFFICE_SUPERUSER' }, mfa: { verified: false, recent: false }, capabilities: { create_project: true, administer_office: false, administer_identity_security: false } }))
    render(<I18nProvider><App /></I18nProvider>)
    fireEvent.click(await screen.findByRole('link', { name: 'Administrar usuarios' }))
    expect(await screen.findByRole('heading', { name: 'Usuarios de Oficina' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Crear proyecto' })).toBeInTheDocument()
    expect(screen.getByText('Creá un proyecto para empezar a invitar personas.')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/virtual-office/admin/usuarios')
  })

  it('denies a classic INTERNAL account when the server returns 403', async () => {
    session.mockRejectedValue({ response: { status: 403 } })
    render(<I18nProvider><App /></I18nProvider>)
    await waitFor(() => expect(window.location.pathname).toBe('/account/security'))
    expect(screen.queryByRole('heading', { name: 'Oficina virtual' })).not.toBeInTheDocument()
  })

  it('lets the initial administrator activate Office with the current password and enters with the new token', async () => {
    session.mockResolvedValueOnce({ data: {
      state: 'BOOTSTRAP_REQUIRED',
      identity: { user_id: 8, tenant_id: 4, account_class: 'INTERNAL', global_role: null },
      bootstrap: { method: 'CURRENT_PASSWORD', target_user_id: 8 },
    } }).mockResolvedValue(allowed())
    bootstrap.mockResolvedValue({ data: { state: 'ACTIVE', target_user_id: 8, verification_method: 'PASSWORD_REAUTH', access_token: 'fresh-office-token' } })
    render(<I18nProvider><App /></I18nProvider>)
    expect(await screen.findByRole('heading', { name: 'Activar Oficina virtual' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Contraseña actual'), { target: { value: 'password-actual' } })
    fireEvent.click(screen.getByRole('button', { name: 'Activar oficina' }))
    await waitFor(() => expect(bootstrap).toHaveBeenCalledWith({ current_password: 'password-actual' }))
    expect(await screen.findByRole('heading', { name: 'Oficina virtual' })).toBeInTheDocument()
    expect(useJaxStore.getState().token).toBe('fresh-office-token')
  })

  it('leaves an external account neutral even when entry is server-approved', async () => {
    useJaxStore.setState({ token: 'external-token', user: { user_id: 8, email: 'client@example.test', account_class: 'EXTERNAL_CLIENT' }, sessionRestoring: false, _sessionEpoch: 1 })
    session.mockResolvedValue(allowed({ identity: { user_id: 8, tenant_id: 4, account_class: 'EXTERNAL_CLIENT', global_role: 'COLLABORATOR' } }))
    render(<I18nProvider><App /></I18nProvider>)
    expect(await screen.findByRole('heading', { name: 'Oficina virtual' })).toBeInTheDocument()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Administrar usuarios' })).not.toBeInTheDocument()
  })

  it('fails closed on server unavailability', async () => {
    session.mockRejectedValue({ response: { status: 503 } })
    render(<I18nProvider><App /></I18nProvider>)
    await waitFor(() => expect(window.location.pathname).toBe('/account/security'))
    expect(screen.queryByRole('heading', { name: 'Oficina virtual' })).not.toBeInTheDocument()
  })

  it('fails closed when the session response is malformed', async () => {
    session.mockResolvedValue({ data: { identity: { user_id: 8 }, mfa: {}, capabilities: {} } })
    render(<I18nProvider><App /></I18nProvider>)
    await waitFor(() => expect(window.location.pathname).toBe('/account/security'))
    expect(screen.queryByRole('heading', { name: 'Oficina virtual' })).not.toBeInTheDocument()
  })

  it('invalidates the Office decision when a data API returns 403', async () => {
    session.mockResolvedValue(allowed())
    projects.list.mockRejectedValue({ response: { status: 403 } })
    render(<I18nProvider><App /></I18nProvider>)
    await waitFor(() => expect(window.location.pathname).toBe('/account/security'))
    expect(screen.queryByRole('heading', { name: 'Oficina virtual' })).not.toBeInTheDocument()
  })

  it('ignores a late approval after the identity changes', async () => {
    let resolve
    session.mockReturnValue(new Promise((done) => { resolve = done }))
    render(<I18nProvider><App /></I18nProvider>)
    useJaxStore.setState({ token: 'new-token', user: { user_id: 9, email: 'new@example.test', account_class: 'INTERNAL' }, _sessionEpoch: 2 })
    resolve(allowed())
    await waitFor(() => expect(session).toHaveBeenCalledTimes(2))
    expect(screen.queryByRole('heading', { name: 'Oficina virtual' })).not.toBeInTheDocument()
  })

  it('removes old Office children synchronously when the session identity changes', async () => {
    session.mockResolvedValueOnce(allowed()).mockReturnValue(new Promise(() => {}))
    projects.list.mockResolvedValue({ data: { projects: [{ id: 12, name: 'Casa anterior', description: '', tenant_id: 4 }], next_after_id: null } })
    render(<I18nProvider><App /></I18nProvider>)
    expect(await screen.findByRole('button', { name: 'Casa anterior' })).toBeInTheDocument()
    act(() => useJaxStore.setState({ token: 'new-token', user: { user_id: 9, email: 'new@example.test', account_class: 'INTERNAL' }, _sessionEpoch: 2 }))
    expect(screen.queryByRole('heading', { name: 'Oficina virtual' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Casa anterior' })).not.toBeInTheDocument()
  })
})
