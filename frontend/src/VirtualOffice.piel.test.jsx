// OP 358 pieza 2: la página de la oficina (post-login) viste la misma piel
// de los studios que el login — MarcaOficina y tarjetas neo-brutalistas — y
// ofrece la salida que antes no existía (esta ruta es standalone: sin el
// botón, dentro no hay logout). El modo instalación se mockea; su lectura
// real de VITE_OFFICE_HOSTS se prueba en lib/instalacionOficina.test.js.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom'
import { I18nProvider } from './i18n/index.jsx'
import { useJaxStore } from './store/useJaxStore'

vi.mock('./apariencia/sincronizarApariencia', () => ({ sincronizarApariencia: vi.fn(() => Promise.resolve()) }))
const instalacionOficina = vi.hoisted(() => vi.fn(() => true))
vi.mock('./lib/instalacionOficina', () => ({ esInstalacionDeOficina: instalacionOficina }))
const session = vi.hoisted(() => vi.fn())
vi.mock('./api/client', () => ({ default: { get: vi.fn(() => new Promise(() => {})), post: vi.fn(() => new Promise(() => {})) } }))
vi.mock('./api/virtualOffice', () => ({
  mfaApi: { status: vi.fn(() => new Promise(() => {})) },
  officeSessionApi: { read: session },
  officeProjectsApi: { list: vi.fn(() => new Promise(() => {})) },
}))

import App from './App.jsx'

describe('Página de la oficina con piel de estudio', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    instalacionOficina.mockReturnValue(true)
    HTMLElement.prototype.scrollIntoView = vi.fn()
    window.history.pushState({}, '', '/virtual-office')
    useJaxStore.setState({ token: 'office-token', user: { user_id: 8, email: 'client@example.test', account_class: 'INTERNAL' }, sessionRestoring: false, _sessionEpoch: 1 })
  })

  it('viste marca y tarjetas del estudio, muestra a la persona y su salida llama al logout del store', async () => {
    session.mockResolvedValue({ data: {
      identity: { user_id: 8, tenant_id: 4, account_class: 'INTERNAL', global_role: 'COLLABORATOR' },
      mfa: { verified: true, recent: true }, capabilities: { create_project: false, administer_office: false, administer_identity_security: false },
    } })
    const logout = vi.fn()
    useJaxStore.setState({ logout })
    render(<I18nProvider><App /></I18nProvider>)
    expect(await screen.findByRole('heading', { name: 'Oficina virtual' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Oficina virtual' })).toBeInTheDocument()
    expect(screen.getByText('client@example.test')).toBeInTheDocument()
    // Las dos secciones (encabezado y proyectos) visten la tarjeta del estudio.
    expect(document.querySelectorAll('.tarjeta-oficina')).toHaveLength(2)
    const salir = screen.getByRole('button', { name: 'Salir' })
    expect(salir).toBeEnabled()
    fireEvent.click(salir)
    expect(logout).toHaveBeenCalledTimes(1)
  })
})
