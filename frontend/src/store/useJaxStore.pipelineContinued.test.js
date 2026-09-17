import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({ default: { post: vi.fn(), get: vi.fn() } }))

import { useJaxStore } from './useJaxStore'

const INICIAL = useJaxStore.getState()

describe('pipeline_continued (spec 2026-09-17 §6.2)', () => {
  beforeEach(() => useJaxStore.setState({ ...INICIAL, token: 't' }, true))

  it('pone el pipeline continuado en activePipelines igual que pipeline_step_changed', () => {
    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_continued',
      payload: { pipeline_id: 'p-9', name: 'leyes', status: 'running', steps: [], run_epoch: 2, pasos_reusados: [0, 1] },
    })
    expect(useJaxStore.getState().activePipelines['p-9']).toMatchObject({ status: 'running', run_epoch: 2 })
  })

  // Adenda ítem 8: si Jacobs mandó basura, faltan campos; no rompe.
  it('un payload sin run_epoch ni pasos igual se registra sin romper', () => {
    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_continued',
      payload: { pipeline_id: 'p-10', status: 'running' },
    })
    expect(useJaxStore.getState().activePipelines['p-10']).toMatchObject({ status: 'running', steps: [] })
  })
})

// Fix round 1 ítems 4-5.
describe('pipeline_continued -- guardas y pasos reusados', () => {
  beforeEach(() => useJaxStore.setState({ ...INICIAL, token: 't' }, true))

  it('sin pipeline_id string, con payload null o steps no lista no toca activePipelines', () => {
    const antes = useJaxStore.getState().activePipelines
    for (const payload of [null, undefined, { status: 'running' }, { pipeline_id: 5, status: 'running' }]) {
      useJaxStore.getState().handleEvent({ event_type: 'pipeline_continued', payload })
      useJaxStore.getState().handleEvent({ event_type: 'pipeline_step_changed', payload })
    }
    expect(useJaxStore.getState().activePipelines).toBe(antes)
    useJaxStore.getState().handleEvent({ event_type: 'pipeline_continued', payload: { pipeline_id: 'p-s', status: 'running', steps: 'basura' } })
    expect(useJaxStore.getState().activePipelines['p-s'].steps).toEqual([])
  })

  it('un continuar con steps vacíos conserva los pasos que ya mostraba el panel', () => {
    const pasos = [{ step_index: 0, facet: 'ada', status: 'completed' }, { step_index: 1, facet: 'thot', status: 'failed' }]
    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_step_changed',
      payload: { pipeline_id: 'p-r', status: 'aborted', steps: pasos },
    })
    const previos = useJaxStore.getState().activePipelines['p-r'].steps
    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_continued',
      payload: { pipeline_id: 'p-r', status: 'running', steps: [], run_epoch: 2, pasos_reusados: [0] },
    })
    const p = useJaxStore.getState().activePipelines['p-r']
    expect(p).toMatchObject({ status: 'running', run_epoch: 2 })
    expect(p.steps).toBe(previos)
    expect(p.steps).toHaveLength(2)
  })
})
