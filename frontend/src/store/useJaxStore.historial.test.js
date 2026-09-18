import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({ default: { post: vi.fn(), get: vi.fn() } }))

import api from '../api/client'
import { useJaxStore } from './useJaxStore'

const INICIAL = useJaxStore.getState()

// Task 9 (2026-09-18, historial-y-arreglos-de-pipeline): hoy el resultado de
// un pipeline se vuelca al chat como mensajes que se van hacia arriba y
// desaparecen -- Fernando no supo qué hacer cuando terminó. cargarHistorial
// pide GET /api/pipelines (ya existe, Task 7) y lo guarda en el store para
// que pages/Historial.jsx lo liste.
describe('cargarHistorial', () => {
  beforeEach(() => {
    useJaxStore.setState({ ...INICIAL, token: 't' }, true)
    api.get.mockReset()
  })

  it('pide /pipelines con offset 0 por defecto y guarda la lista', async () => {
    const pipelines = [
      { pipeline_id: 'p1', name: 'uno', status: 'completed', created_at: 1000, updated_at: 1010, causa: null, duracion_s: 10, costo_usd: null },
    ]
    api.get.mockResolvedValue({ data: { pipelines, has_more: false } })

    await useJaxStore.getState().cargarHistorial()

    expect(api.get).toHaveBeenCalledWith('/pipelines', { params: { offset: 0 } })
    expect(useJaxStore.getState().historial.pipelines).toEqual(pipelines)
    expect(useJaxStore.getState().historial.hasMore).toBe(false)
    expect(useJaxStore.getState().historial.cargando).toBe(false)
    expect(useJaxStore.getState().historial.error).toBe(false)
  })

  it('con offset > 0 agrega a la lista existente en vez de reemplazarla', async () => {
    useJaxStore.setState({
      historial: { pipelines: [{ pipeline_id: 'p0', name: 'viejo', status: 'completed' }], hasMore: true, cargando: false, error: false },
    })
    const nuevos = [{ pipeline_id: 'p1', name: 'nuevo', status: 'completed' }]
    api.get.mockResolvedValue({ data: { pipelines: nuevos, has_more: false } })

    await useJaxStore.getState().cargarHistorial({ offset: 50 })

    expect(api.get).toHaveBeenCalledWith('/pipelines', { params: { offset: 50 } })
    expect(useJaxStore.getState().historial.pipelines.map((p) => p.pipeline_id)).toEqual(['p0', 'p1'])
    expect(useJaxStore.getState().historial.hasMore).toBe(false)
  })

  it('con offset 0 (recarga) reemplaza la lista, no la duplica', async () => {
    useJaxStore.setState({
      historial: { pipelines: [{ pipeline_id: 'p0', name: 'viejo', status: 'completed' }], hasMore: false, cargando: false, error: false },
    })
    const frescos = [{ pipeline_id: 'p9', name: 'fresco', status: 'running' }]
    api.get.mockResolvedValue({ data: { pipelines: frescos, has_more: false } })

    await useJaxStore.getState().cargarHistorial()

    expect(useJaxStore.getState().historial.pipelines).toEqual(frescos)
  })

  it('un fallo de red marca error y NO borra lo que ya se había cargado', async () => {
    useJaxStore.setState({
      historial: { pipelines: [{ pipeline_id: 'p0', name: 'viejo', status: 'completed' }], hasMore: false, cargando: false, error: false },
    })
    api.get.mockRejectedValue(new Error('network'))

    await useJaxStore.getState().cargarHistorial()

    expect(useJaxStore.getState().historial.error).toBe(true)
    expect(useJaxStore.getState().historial.cargando).toBe(false)
    expect(useJaxStore.getState().historial.pipelines).toHaveLength(1)
  })

  it('una respuesta sin forma válida (pipelines ausente) no revienta: lista vacía', async () => {
    api.get.mockResolvedValue({ data: {} })

    await useJaxStore.getState().cargarHistorial()

    expect(useJaxStore.getState().historial.pipelines).toEqual([])
    expect(useJaxStore.getState().historial.error).toBe(false)
  })

  it('cargando queda en true mientras el fetch está en vuelo', async () => {
    let resolver
    api.get.mockReturnValue(new Promise((r) => { resolver = r }))

    const promesa = useJaxStore.getState().cargarHistorial()
    expect(useJaxStore.getState().historial.cargando).toBe(true)

    resolver({ data: { pipelines: [], has_more: false } })
    await promesa
    expect(useJaxStore.getState().historial.cargando).toBe(false)
  })
})
