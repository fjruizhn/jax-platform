import { render, screen, fireEvent, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'

// Task 9 (2026-09-18, historial-y-arreglos-de-pipeline): "debería haber una
// forma de ver el resultado del pipeline... un lugar donde se listen los
// pipelines que se hicieron" (pedido textual de Fernando). GET /api/pipelines
// ya existe completo (Task 7): esta pantalla sólo lo lista y, por fila, abre
// el detalle completo (DetallePipeline).
vi.mock('../api/client', () => ({ default: { get: vi.fn() } }))

import api from '../api/client'
import Historial from './Historial'
import { I18nProvider } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'

const INICIAL = useJaxStore.getState()

function renderHistorial() {
  return render(<I18nProvider><MemoryRouter><Historial /></MemoryRouter></I18nProvider>)
}

const PIPELINES = [
  { pipeline_id: 'p1', name: 'plan de leyes', status: 'completed', created_at: 1758000000, updated_at: 1758000010, causa: null, duracion_s: 10.2, costo_usd: null },
  { pipeline_id: 'p2', name: 'otro plan', status: 'running', created_at: 1758000100, updated_at: 1758000100, causa: null, duracion_s: null, costo_usd: null },
]

beforeEach(() => {
  useJaxStore.setState({ ...INICIAL, token: 't' }, true)
  api.get.mockReset()
})

describe('Historial', () => {
  it('al montar, pide la lista (offset 0) y la muestra', async () => {
    api.get.mockResolvedValue({ data: { pipelines: PIPELINES, has_more: false } })
    renderHistorial()
    expect(await screen.findByText('plan de leyes')).toBeInTheDocument()
    expect(screen.getByText('otro plan')).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledWith('/pipelines', { params: { offset: 0 } })
  })

  it('muestra el estado traducido de cada pipeline, no el valor crudo', async () => {
    api.get.mockResolvedValue({ data: { pipelines: PIPELINES, has_more: false } })
    renderHistorial()
    await screen.findByText('plan de leyes')
    expect(screen.getByText('Completado')).toBeInTheDocument()
    expect(screen.getByText('En curso')).toBeInTheDocument()
  })

  it('duracion_s null se muestra como "desconocido", nunca como 0', async () => {
    api.get.mockResolvedValue({ data: { pipelines: PIPELINES, has_more: false } })
    renderHistorial()
    await screen.findByText('otro plan')
    const fila = screen.getByText('otro plan').closest('tr')
    const celdaDuracion = fila.querySelector('[data-campo="duracion"]')
    expect(celdaDuracion).toHaveTextContent('desconocido')
    expect(within(fila).queryByText('0')).not.toBeInTheDocument()
  })

  it('costo_usd sale "desconocido" (limitación de esquema conocida, no se muestra como $0)', async () => {
    api.get.mockResolvedValue({ data: { pipelines: [PIPELINES[0]], has_more: false } })
    renderHistorial()
    const fila = (await screen.findByText('plan de leyes')).closest('tr')
    const celdaCosto = fila.querySelector('[data-campo="costo"]')
    expect(celdaCosto).toHaveTextContent('desconocido')
  })

  it('sin pipelines, muestra el estado vacío', async () => {
    api.get.mockResolvedValue({ data: { pipelines: [], has_more: false } })
    renderHistorial()
    expect(await screen.findByText('Todavía no corriste ningún pipeline.')).toBeInTheDocument()
  })

  it('un fallo de red muestra el error, con un botón para reintentar', async () => {
    api.get.mockRejectedValue(new Error('network'))
    renderHistorial()
    expect(await screen.findByText('No se pudo cargar el historial. Probá de nuevo.')).toBeInTheDocument()

    api.get.mockResolvedValue({ data: { pipelines: PIPELINES, has_more: false } })
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }))
    expect(await screen.findByText('plan de leyes')).toBeInTheDocument()
  })

  it('con has_more, "Cargar más" pide la página siguiente y agrega filas', async () => {
    api.get.mockResolvedValueOnce({ data: { pipelines: PIPELINES, has_more: true } })
    renderHistorial()
    await screen.findByText('plan de leyes')

    const pagina2 = [{ pipeline_id: 'p3', name: 'tercero', status: 'completed', created_at: 1758000200, updated_at: 1758000210, causa: null, duracion_s: 10, costo_usd: null }]
    api.get.mockResolvedValueOnce({ data: { pipelines: pagina2, has_more: false } })
    fireEvent.click(screen.getByRole('button', { name: 'Cargar más' }))

    expect(await screen.findByText('tercero')).toBeInTheDocument()
    expect(screen.getByText('plan de leyes')).toBeInTheDocument() // no se pierde lo anterior
    expect(api.get).toHaveBeenLastCalledWith('/pipelines', { params: { offset: 2 } })
  })

  it('sin has_more, no aparece el botón "Cargar más"', async () => {
    api.get.mockResolvedValue({ data: { pipelines: PIPELINES, has_more: false } })
    renderHistorial()
    await screen.findByText('plan de leyes')
    expect(screen.queryByRole('button', { name: 'Cargar más' })).not.toBeInTheDocument()
  })

  it('"Ver detalle" en una fila abre el detalle de ESE pipeline', async () => {
    api.get.mockResolvedValue({ data: { pipelines: PIPELINES, has_more: false } })
    renderHistorial()
    await screen.findByText('plan de leyes')

    api.get.mockResolvedValue({
      data: { pipeline_id: 'p1', name: 'plan de leyes', status: 'completed', total_duration_seconds: 10.2, steps: [] },
    })
    const fila = screen.getByText('plan de leyes').closest('tr')
    fireEvent.click(within(fila).getByRole('button', { name: 'Ver detalle' }))

    expect(await screen.findByText('Detalle — plan de leyes')).toBeInTheDocument()
    expect(api.get).toHaveBeenLastCalledWith('/pipelines/p1/results')
  })

  it('cerrar el detalle lo saca de la pantalla sin recargar la lista', async () => {
    api.get.mockResolvedValue({ data: { pipelines: PIPELINES, has_more: false } })
    renderHistorial()
    await screen.findByText('plan de leyes')

    api.get.mockResolvedValue({
      data: { pipeline_id: 'p1', name: 'plan de leyes', status: 'completed', total_duration_seconds: 10.2, steps: [] },
    })
    const fila = screen.getByText('plan de leyes').closest('tr')
    fireEvent.click(within(fila).getByRole('button', { name: 'Ver detalle' }))
    await screen.findByText('Detalle — plan de leyes')

    fireEvent.click(screen.getByRole('button', { name: 'Cerrar detalle' }))
    expect(screen.queryByText('Detalle — plan de leyes')).not.toBeInTheDocument()
    expect(screen.getByText('plan de leyes')).toBeInTheDocument()
  })
})
