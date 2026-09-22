import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Task 6 (2026-09-22, spec descartar-pipelines §5): pestaña "Descartados" en
// el historial -- Recuperar por fila y, sólo para el superadmin, Borrar
// (ocultar) con ConfirmacionSuma. Los descartados NO comparten el store
// `historial` (spec §2: "van a ser muchos en el tiempo", vista propia).
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

import api from '../../api/client'
import HistorialContenido from './HistorialContenido'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import es from '../../i18n/es.js'

const INICIAL = useJaxStore.getState()

const DESCARTADOS = [
  { pipeline_id: 'd1', name: 'plan descartado', status: 'discarded', descartado_at: 1758000500, costo_usd: 0.01 },
]

function renderCuerpo(extra = {}) {
  return render(
    <I18nProvider>
      <HistorialContenido pipelineId={null} nombreSeleccionado={null}
        onSelect={vi.fn()} onCloseDetail={vi.fn()} {...extra} />
    </I18nProvider>
  )
}

function mockGet({ todos = [], discarded = DESCARTADOS, hayMasDescartados = false } = {}) {
  api.get.mockImplementation((url, config) => {
    if (url !== '/pipelines') return Promise.reject(new Error(`url inesperada: ${url}`))
    if (config?.params?.estado === 'discarded') {
      return Promise.resolve({ data: { pipelines: discarded, has_more: hayMasDescartados } })
    }
    return Promise.resolve({ data: { pipelines: todos, has_more: false } })
  })
}

beforeEach(() => {
  useJaxStore.setState({ ...INICIAL, token: 't', user: { user_id: '1', role: 'viewer' } }, true)
  api.get.mockReset()
  api.post.mockReset()
  mockGet()
})

describe('HistorialContenido -- pestaña Descartados (Task 6)', () => {
  it('hay dos pestañas y "Todos" es la inicial', async () => {
    renderCuerpo()
    expect(screen.getByRole('button', { name: es.pestanaTodos })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: es.pestanaDescartados })).toBeInTheDocument()
    // "Todos" es la inicial: pide la lista normal (store historial), no la de descartados.
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/pipelines', { params: { offset: 0 } }))
    expect(api.get).not.toHaveBeenCalledWith('/pipelines', { params: { estado: 'discarded', limite: 50, offset: 0 } })
  })

  it('"Descartados" pide GET /pipelines?estado=discarded&limite=50&offset=0 y lista con su fecha de descarte', async () => {
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))

    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/pipelines', {
      params: { estado: 'discarded', limite: 50, offset: 0 },
    }))
    expect(await screen.findByText('plan descartado')).toBeInTheDocument()
    const fila = screen.getByText('plan descartado').closest('tr')
    const celdaFecha = within(fila).getByText((_, el) => el?.getAttribute('data-campo') === 'descartado')
    expect(celdaFecha).not.toHaveTextContent('—')
  })

  it('con has_more, "Cargar más" pide offset=50', async () => {
    mockGet({ hayMasDescartados: true })
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    await screen.findByText('plan descartado')

    mockGet({ discarded: [{ pipeline_id: 'd2', name: 'segundo', status: 'discarded', descartado_at: 1758000600, costo_usd: null }], hayMasDescartados: false })
    fireEvent.click(screen.getByRole('button', { name: es.cargarMas }))

    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/pipelines', {
      params: { estado: 'discarded', limite: 50, offset: 1 },
    }))
    expect(await screen.findByText('segundo')).toBeInTheDocument()
    expect(screen.getByText('plan descartado')).toBeInTheDocument() // no se pierde lo anterior
  })

  it('Recuperar llama a recover y quita la fila', async () => {
    api.post.mockResolvedValue({ data: { pipeline_id: 'd1', status: 'aborted' } })
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    await screen.findByText('plan descartado')

    fireEvent.click(screen.getByRole('button', { name: es.recuperarPipeline }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/pipelines/d1/recover'))
    await waitFor(() => expect(screen.queryByText('plan descartado')).not.toBeInTheDocument())
  })

  it('Borrar aparece solo para el superadmin, y al confirmar llama a hide', async () => {
    useJaxStore.setState({ user: { user_id: '1', role: 'superadmin' } })
    api.post.mockResolvedValue({ data: { pipeline_id: 'd1', status: 'hidden' } })
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    await screen.findByText('plan descartado')

    fireEvent.click(screen.getByRole('button', { name: es.borrarPipeline }))
    const dialogo = screen.getByRole('dialog')
    const [, a, b] = within(dialogo).getByText(/Resolvé \d+ \+ \d+ = \?/).textContent.match(/(\d+) \+ (\d+)/)
    fireEvent.change(within(dialogo).getByLabelText(/Resolvé/), { target: { value: String(Number(a) + Number(b)) } })
    fireEvent.click(within(dialogo).getByRole('button', { name: es.borrarPipeline }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/pipelines/d1/hide'))
    await waitFor(() => expect(screen.queryByText('plan descartado')).not.toBeInTheDocument())
  })

  it('un usuario que no es superadmin no ve "Borrar"', async () => {
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    await screen.findByText('plan descartado')
    expect(screen.queryByRole('button', { name: es.borrarPipeline })).not.toBeInTheDocument()
  })

  it('la lista vacía muestra el estado vacío', async () => {
    mockGet({ discarded: [] })
    renderCuerpo()
    fireEvent.click(screen.getByRole('button', { name: es.pestanaDescartados }))
    expect(await screen.findByText(es.sinDescartados)).toBeInTheDocument()
  })
})
