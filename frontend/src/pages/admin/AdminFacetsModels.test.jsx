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

function resolverSuma(dialogo) {
  const [, a, b] = within(dialogo).getByText(/Resolvé \d+ \+ \d+ = \?/).textContent.match(/(\d+) \+ (\d+)/)
  fireEvent.change(within(dialogo).getByRole('spinbutton'), { target: { value: String(Number(a) + Number(b)) } })
}

beforeEach(() => {
  localStorage.clear()
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
})
