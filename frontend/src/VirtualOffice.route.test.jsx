import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { I18nProvider } from './i18n/index.jsx'
import { useJaxStore } from './store/useJaxStore'

vi.mock('./apariencia/sincronizarApariencia', () => ({ sincronizarApariencia: vi.fn(() => Promise.resolve()) }))
const session = vi.hoisted(() => vi.fn())
vi.mock('./api/client', () => ({ default: { get: vi.fn(() => new Promise(() => {})), post: vi.fn(() => new Promise(() => {})) } }))
vi.mock('./api/virtualOffice', () => ({
  mfaApi: { status: vi.fn(() => new Promise(() => {})) },
  officeSessionApi: { read: session },
}))

import App from './App.jsx'

describe('Virtual Office route', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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

  it('denies a classic INTERNAL account when the server returns 403', async () => {
    session.mockRejectedValue({ response: { status: 403 } })
    render(<I18nProvider><App /></I18nProvider>)
    await waitFor(() => expect(window.location.pathname).toBe('/account/security'))
    expect(screen.queryByRole('heading', { name: 'Oficina virtual' })).not.toBeInTheDocument()
  })

  it('leaves an external account neutral even when entry is server-approved', async () => {
    useJaxStore.setState({ token: 'external-token', user: { user_id: 8, email: 'client@example.test', account_class: 'EXTERNAL_CLIENT' }, sessionRestoring: false, _sessionEpoch: 1 })
    session.mockResolvedValue(allowed({ identity: { user_id: 8, tenant_id: 4, account_class: 'EXTERNAL_CLIENT', global_role: 'COLLABORATOR' } }))
    render(<I18nProvider><App /></I18nProvider>)
    expect(await screen.findByRole('heading', { name: 'Oficina virtual' })).toBeInTheDocument()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
  })

  it('fails closed on server unavailability', async () => {
    session.mockRejectedValue({ response: { status: 503 } })
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
})
