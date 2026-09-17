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
