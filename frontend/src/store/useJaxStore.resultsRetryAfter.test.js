import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({ default: { post: vi.fn(), get: vi.fn() } }))

import api from '../api/client'
import { useJaxStore } from './useJaxStore'

const INICIAL = useJaxStore.getState()

// 2026-09-17 (jax#212): la reserva de cupo se hace en la escritura misma y, bajo
// contención, Jacobs responde 503 `contencion_al_reservar` con `Retry-After` en
// segundos. El único reintento automático de la Mesa es el del fetch de
// resultados: si llega ese 503, tiene que esperar lo que dice el servidor y no
// su demora fija de 2 s. Un cliente con demora fija no se entera si el techo de
// espera del servidor cambia.
// El evento de pipeline completado dispara más de un GET (la lista y los
// resultados): se cuentan SÓLO los de resultados, que es el que reintenta.
function llamadasDeResultados(pipelineId) {
  return api.get.mock.calls.filter(([url]) => url === `/pipelines/${pipelineId}/results`).length
}

function error503(retryAfter) {
  const err = new Error('contención')
  err.response = { status: 503, headers: { 'retry-after': retryAfter }, data: { detail: { code: 'contencion_al_reservar' } } }
  return err
}

describe('reintento del fetch de resultados con Retry-After', () => {
  beforeEach(() => useJaxStore.setState({ ...INICIAL, token: 't' }, true))

  it('un 503 con Retry-After espera esos segundos, no la demora fija', async () => {
    vi.useFakeTimers()
    try {
      api.get.mockImplementation((url) => (url.endsWith('/results')
        ? (llamadasDeResultados('p-503') === 1 ? Promise.reject(error503('5')) : Promise.resolve({ data: { steps: [] } }))
        : Promise.resolve({ data: {} })))

      useJaxStore.getState().handleEvent({
        event_type: 'pipeline_step_changed',
        payload: { pipeline_id: 'p-503', status: 'completed' },
      })
      await vi.advanceTimersByTimeAsync(0)
      expect(llamadasDeResultados('p-503')).toBe(1)

      // a los 2 s (la demora fija) todavía NO reintenta: el servidor pidió 5
      await vi.advanceTimersByTimeAsync(2000)
      expect(llamadasDeResultados('p-503')).toBe(1)

      await vi.advanceTimersByTimeAsync(3000)
      expect(llamadasDeResultados('p-503')).toBe(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('un error sin Retry-After sigue con la demora fija de 2 s', async () => {
    vi.useFakeTimers()
    try {
      api.get.mockImplementation((url) => (url.endsWith('/results')
        ? (llamadasDeResultados('p-red') === 1 ? Promise.reject(new Error('red caída')) : Promise.resolve({ data: { steps: [] } }))
        : Promise.resolve({ data: {} })))

      useJaxStore.getState().handleEvent({
        event_type: 'pipeline_step_changed',
        payload: { pipeline_id: 'p-red', status: 'completed' },
      })
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(2000)
      expect(llamadasDeResultados('p-red')).toBe(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('un Retry-After absurdo o ilegible no deja el reintento colgado', async () => {
    vi.useFakeTimers()
    try {
      api.get.mockImplementation((url) => (url.endsWith('/results')
        ? (llamadasDeResultados('p-absurdo') === 1 ? Promise.reject(error503('99999')) : Promise.resolve({ data: { steps: [] } }))
        : Promise.resolve({ data: {} })))

      useJaxStore.getState().handleEvent({
        event_type: 'pipeline_step_changed',
        payload: { pipeline_id: 'p-absurdo', status: 'completed' },
      })
      await vi.advanceTimersByTimeAsync(0)
      // el techo propio de la Mesa: 30 s
      await vi.advanceTimersByTimeAsync(30000)
      expect(llamadasDeResultados('p-absurdo')).toBe(2)
    } finally {
      vi.useRealTimers()
    }
  })
})
