import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  default: {
    post: vi.fn(),
    get: vi.fn(),
  },
}))

import api from '../api/client'
import { useJaxStore, getEyeState } from './useJaxStore'

const INITIAL_STATE = useJaxStore.getState()

// Task 20, ronda de arreglos 1 (revisión de e31e3dc): /api/state y el evento
// facet_status_changed mandan cada faceta SIN `token` (con el `color` hex
// viejo del backend). El store tiene que derivar el token de la CLAVE de la
// faceta al recibir datos del servidor; si no, FacetCard y el ojo HAL caen en
// gris. Tampoco se guarda el hex del servidor: nada pinta con él.
describe('el token de faceta se deriva de la clave, no de los datos del servidor', () => {
  beforeEach(() => {
    useJaxStore.setState({ ...INITIAL_STATE, token: 'test-token', user: { user_id: 1 } }, true)
    vi.clearAllMocks()
  })

  it('loadState: una faceta del servidor sin token queda con el token de su clave', async () => {
    api.get.mockResolvedValueOnce({
      data: {
        facets: { hyde: { name: 'hyde', status: 'thinking', last_message: 'x', color: '#abc' } },
        active_pipelines: {},
        las_manos_alive: true,
      },
    })
    await useJaxStore.getState().loadState()

    const { facets, activePipelines, lasManos, killSwitchActive } = useJaxStore.getState()
    expect(facets.hyde.token).toBe('faceta-hyde')
    expect(facets.hyde.status).toBe('thinking')
    expect(facets.hyde.last_message).toBe('x')
    expect(facets.hyde).not.toHaveProperty('color')
    expect(facets.jekyll.token).toBe('faceta-jekyll') // las que no vinieron siguen con el default
    expect(getEyeState(facets, activePipelines, lasManos, killSwitchActive).token).toBe('faceta-hyde')
  })

  it('loadState: una faceta que el tema no conoce cae en el respaldo, no en undefined', async () => {
    api.get.mockResolvedValueOnce({
      data: { facets: { claude: { name: 'claude', status: 'idle', color: '#123456' } }, active_pipelines: {}, las_manos_alive: true },
    })
    await useJaxStore.getState().loadState()
    expect(useJaxStore.getState().facets.claude.token).toBe('texto-suave')
  })

  it('facet_status_changed: una faceta que el store no tenía queda con el token de su clave', () => {
    const { jekyll, ...sinJekyll } = useJaxStore.getState().facets
    expect(jekyll).toBeDefined()
    useJaxStore.setState({ facets: sinJekyll })

    useJaxStore.getState().handleEvent({
      event_type: 'facet_status_changed',
      payload: { facet: 'jekyll', status: 'thinking', message: 'hola' },
    })

    const { facets } = useJaxStore.getState()
    expect(facets.jekyll.token).toBe('faceta-jekyll')
    expect(facets.jekyll.status).toBe('thinking')
    expect(facets.jekyll.last_message).toBe('hola')
    expect(getEyeState(facets, {}, true, false).token).toBe('faceta-jekyll')
  })
})
