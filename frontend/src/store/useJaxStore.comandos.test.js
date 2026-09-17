import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({ default: { post: vi.fn(), get: vi.fn() } }))

import api from '../api/client'
import { useJaxStore, contenidoDeComando } from './useJaxStore'
import es from '../i18n/es.js'

const INICIAL = useJaxStore.getState()

beforeEach(() => {
  localStorage.clear()
  useJaxStore.setState({ ...INICIAL, token: 't', user: { user_id: 1 } }, true)
  vi.clearAllMocks()
})

function conPendiente(taskId) {
  useJaxStore.setState({ messages: [{ id: `cmd-${taskId}`, facet: 'hyde', content: '…', status: 'running', timestamp: 't' }] })
  localStorage.setItem('jax_pending_cmds', JSON.stringify({ owner: 1, ids: [taskId] }))
}

const pendientes = () => JSON.parse(localStorage.getItem('jax_pending_cmds')).ids

describe('contenidoDeComando (A-53)', () => {
  it('cada código tiene su texto', () => {
    expect(contenidoDeComando(es, { code: 'comando_fallo', motivo: 'sin binario' })).toBe(es.commandFailed('sin binario'))
    expect(contenidoDeComando(es, { code: 'comando_sin_resultado', result: '' })).toBe(es.commandNoResult)
    expect(contenidoDeComando(es, { code: 'comando_simulado', result: 'mision' })).toBe(es.commandDryRun('mision'))
    expect(contenidoDeComando(es, { result: 'listo' })).toBe('listo')
  })
})

describe('command_completed y checkPendingTasks con códigos (A-44)', () => {
  it('un fallo por WS queda failed con el texto traducido y sale de pendientes', () => {
    conPendiente('t1')
    useJaxStore.getState().handleEvent({ event_type: 'command_completed',
      payload: { task_id: 't1', status: 'failed', code: 'comando_fallo', result: '', motivo: 'x' } })
    const msg = useJaxStore.getState().messages[0]
    expect([msg.status, msg.content]).toEqual(['failed', es.commandFailed('x')])
    expect(pendientes()).toEqual([])
  })

  it('un completado por WS sin resultado ni código pide el GET y traduce su código', async () => {
    conPendiente('t0')
    api.get.mockResolvedValue({ data: { status: 'completed', result: '', code: 'comando_sin_resultado' } })
    useJaxStore.getState().handleEvent({ event_type: 'command_completed',
      payload: { task_id: 't0', status: 'completed' } })
    await new Promise((r) => setTimeout(r, 0))
    const msg = useJaxStore.getState().messages[0]
    expect([msg.status, msg.content]).toEqual(['completed', es.commandNoResult])
    expect(pendientes()).toEqual([])
  })

  it('un completado SIN resultado deja de consultarse (antes quedaba running para siempre)', async () => {
    conPendiente('t2')
    api.get.mockResolvedValue({ data: { status: 'completed', result: '', code: 'comando_sin_resultado' } })
    await useJaxStore.getState().checkPendingTasks()
    const msg = useJaxStore.getState().messages[0]
    expect([msg.status, msg.content]).toEqual(['completed', es.commandNoResult])
    expect(pendientes()).toEqual([])
  })

  it('un fallo consultado por GET queda failed con el motivo (antes quedaba running para siempre)', async () => {
    conPendiente('t3')
    api.get.mockResolvedValue({ data: { status: 'failed', result: '', code: 'comando_fallo', motivo: 'sin binario' } })
    await useJaxStore.getState().checkPendingTasks()
    const msg = useJaxStore.getState().messages[0]
    expect([msg.status, msg.content]).toEqual(['failed', es.commandFailed('sin binario')])
    expect(pendientes()).toEqual([])
  })

  it('una simulación consultada por GET muestra la misión como simulación', async () => {
    conPendiente('t4')
    api.get.mockResolvedValue({ data: { status: 'completed', result: 'mision', code: 'comando_simulado' } })
    await useJaxStore.getState().checkPendingTasks()
    const msg = useJaxStore.getState().messages[0]
    expect([msg.status, msg.content]).toEqual(['completed', es.commandDryRun('mision')])
    expect(pendientes()).toEqual([])
  })

  it('una tarea todavía en curso sigue pendiente', async () => {
    vi.useFakeTimers()
    try {
      conPendiente('t5')
      api.get.mockResolvedValue({ data: { status: 'running' } })
      await useJaxStore.getState().checkPendingTasks()
      expect(useJaxStore.getState().messages[0].status).toBe('running')
      expect(pendientes()).toEqual(['t5'])
    } finally {
      vi.clearAllTimers()
      vi.useRealTimers()
    }
  })
})
