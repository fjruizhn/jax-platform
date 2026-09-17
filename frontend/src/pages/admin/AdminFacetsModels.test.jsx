import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

vi.mock('../../api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
vi.mock('./AdminModelCatalog', () => ({ default: () => null }))
vi.mock('./AdminFacetBindings', () => ({ default: () => null }))
vi.mock('./AdminMotors', () => ({ default: () => null }))

import api from '../../api/client'
import AdminFacetsModels from './AdminFacetsModels'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import { useJaxStore } from '../../store/useJaxStore'

function resolverSuma(dialogo) {
  const [, a, b] = within(dialogo).getByText(/Resolvé \d+ \+ \d+ = \?/).textContent.match(/(\d+) \+ (\d+)/)
  fireEvent.change(within(dialogo).getByRole('spinbutton'), { target: { value: String(Number(a) + Number(b)) } })
}

beforeEach(() => {
  localStorage.clear()
  useJaxStore.setState({ toasts: [] })
  api.post.mockReset()
  api.get.mockImplementation((url) => Promise.resolve({ data: url === '/admin/keys'
    ? { providers: [{ id: 'openai', name: 'OpenAI', has_key: true, key_last4: '1234' }] }
    : { providers: [{ id: 'openai', credentials: [{ state: 'active', last_health_status: 'ok' }] }] } }))
})

describe('AdminFacetsModels -- credenciales (A-23)', () => {
  it('revocar pide la suma antes de llamar a la API', async () => {
    api.post.mockResolvedValue({ data: {} })
    render(<I18nProvider><AdminFacetsModels /></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: es.adminKeyRevoke }))
    const dialogo = screen.getByRole('dialog')
    const confirmar = within(dialogo).getByRole('button', { name: es.adminKeyRevoke })
    expect(confirmar).toBeDisabled()
    expect(api.post).not.toHaveBeenCalled()
    resolverSuma(dialogo)
    fireEvent.click(confirmar)
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/credentials/openai/revoke'))
  })

  it('un fallo al revocar deja el diálogo abierto', async () => {
    api.post.mockRejectedValue({ response: { status: 500 } })
    render(<I18nProvider><AdminFacetsModels /></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: es.adminKeyRevoke }))
    const dialogo = screen.getByRole('dialog')
    resolverSuma(dialogo)
    fireEvent.click(within(dialogo).getByRole('button', { name: es.adminKeyRevoke }))
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('rotar abre un Dialogo con nombre', async () => {
    render(<I18nProvider><AdminFacetsModels /></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: es.adminKeyRotate }))
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true')
  })

  it('un fallo al probar la llave dice un texto de i18n, no "Error" literal', async () => {
    api.post.mockRejectedValue({ response: { status: 500 } })
    render(<I18nProvider><AdminFacetsModels /></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: es.adminKeyTest }))
    expect(await screen.findByText(`${es.adminKeyFail}: ${es.adminKeyTestError}`)).toBeInTheDocument()
  })

  // R11 (fix round 1, Task 13, 2026-09-16): handleRevoke limpia `revokeConfirm`
  // (cierra el Dialogo/ConfirmacionSuma) y `revoking` (rehabilita este mismo
  // botón) en el mismo tramo de setState tras el `await api.post`. Es el
  // mismo patrón que rompía en AdminSmtp.jsx: Dialogo intenta devolver el
  // foco al disparador en el instante en que React todavía no aplicó, en ese
  // commit, la mutación que lo saca de `disabled` -- sin el arreglo en
  // Dialogo, el foco se pierde en el body.
  it('revocar con éxito devuelve el foco al botón que abrió el diálogo, aunque quede disabled en el mismo commit que cierra', async () => {
    api.post.mockResolvedValue({ data: {} })
    render(<I18nProvider><AdminFacetsModels /></I18nProvider>)
    const disparador = await screen.findByRole('button', { name: es.adminKeyRevoke })
    // fireEvent.click no simula el foco-al-clic de un navegador real (jsdom);
    // se enfoca a mano, mismo patrón que Dialogo.test.jsx y AdminUsers.test.jsx.
    disparador.focus()
    fireEvent.click(disparador)
    const dialogo = screen.getByRole('dialog')
    resolverSuma(dialogo)
    fireEvent.click(within(dialogo).getByRole('button', { name: es.adminKeyRevoke }))
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(disparador).toHaveFocus())
  })

  // Ronda final M6 (2026-09-16): el fallo de rotar y el de revocar se avisan
  // con un toast de i18n (antes no había test que lo fijara).
  it('un fallo al rotar avisa con el toast de i18n y deja el diálogo abierto', async () => {
    api.post.mockRejectedValue({ response: { status: 500 } })
    render(<I18nProvider><AdminFacetsModels /></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: es.adminKeyRotate }))
    const dialogo = screen.getByRole('dialog')
    fireEvent.change(within(dialogo).getByPlaceholderText(es.adminKeyNewValue), { target: { value: 'sk-nueva' } })
    fireEvent.click(within(dialogo).getByRole('button', { name: es.adminKeySave }))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/admin/credentials/openai/rotate', { api_key: 'sk-nueva' }))
    await waitFor(() => expect(useJaxStore.getState().toasts).toEqual([
      expect.objectContaining({ type: 'error', message: es.adminKeyRotateError })]))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('un fallo al revocar avisa con el toast de i18n', async () => {
    api.post.mockRejectedValue({ response: { status: 500 } })
    render(<I18nProvider><AdminFacetsModels /></I18nProvider>)
    fireEvent.click(await screen.findByRole('button', { name: es.adminKeyRevoke }))
    const dialogo = screen.getByRole('dialog')
    resolverSuma(dialogo)
    fireEvent.click(within(dialogo).getByRole('button', { name: es.adminKeyRevoke }))
    await waitFor(() => expect(useJaxStore.getState().toasts).toEqual([
      expect.objectContaining({ type: 'error', message: es.adminKeyRevokeError })]))
  })
})
