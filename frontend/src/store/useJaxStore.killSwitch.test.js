import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({ default: { post: vi.fn(), get: vi.fn() } }))

import api from '../api/client'
import { useJaxStore } from './useJaxStore'
import es from '../i18n/es.js'

const INICIAL = useJaxStore.getState()

describe('useJaxStore -- kill switch real', () => {
  beforeEach(() => {
    useJaxStore.setState(INICIAL, true)
    vi.clearAllMocks()
  })

  it('loadState toma el estado real del freno', async () => {
    api.get.mockResolvedValue({ data: { facets: {}, active_pipelines: {}, las_manos_alive: true, kill_switch_active: true } })
    await useJaxStore.getState().loadState()
    expect(useJaxStore.getState().killSwitchActive).toBe(true)
  })

  it('kill_switch_released lo apaga y avisa', () => {
    useJaxStore.setState({ killSwitchActive: true })
    useJaxStore.getState().handleEvent({ event_type: 'kill_switch_released', payload: { activo: false } })
    expect(useJaxStore.getState().killSwitchActive).toBe(false)
    expect(useJaxStore.getState().toasts.at(-1).message).toBe(es.killSwitchReleasedToast)
  })

  it('activarKillSwitch no enciende nada si el backend falla, y lo propaga', async () => {
    api.post.mockRejectedValue(new Error('x'))
    await expect(useJaxStore.getState().activarKillSwitch()).rejects.toThrow('x')
    expect(useJaxStore.getState().killSwitchActive).toBe(false)
  })

  it('reanudarKillSwitch apaga según la respuesta', async () => {
    useJaxStore.setState({ killSwitchActive: true })
    api.post.mockResolvedValue({ data: { activo: false, cambio: true } })
    await useJaxStore.getState().reanudarKillSwitch()
    expect(api.post).toHaveBeenCalledWith('/admin/kill-switch/reanudar')
    expect(useJaxStore.getState().killSwitchActive).toBe(false)
  })

  // Ruling R5 (2026-09-17, P1 de Fernando): un 500 kill_switch_auditoria_fallida
  // dice que el freno QUEDÓ PUESTO (activar y reanudar). El estado lo refleja
  // antes de propagar el error; cualquier otro error no toca el estado.
  const AUDITORIA_FALLIDA = { response: { status: 500, data: { detail: 'kill_switch_auditoria_fallida' } } }

  it('activarKillSwitch con la auditoría fallida deja el freno puesto y lo propaga', async () => {
    api.post.mockRejectedValue(AUDITORIA_FALLIDA)
    await expect(useJaxStore.getState().activarKillSwitch()).rejects.toBe(AUDITORIA_FALLIDA)
    expect(useJaxStore.getState().killSwitchActive).toBe(true)
  })

  it('reanudarKillSwitch con la auditoría fallida deja el freno puesto y lo propaga', async () => {
    useJaxStore.setState({ killSwitchActive: false })
    api.post.mockRejectedValue(AUDITORIA_FALLIDA)
    await expect(useJaxStore.getState().reanudarKillSwitch()).rejects.toBe(AUDITORIA_FALLIDA)
    expect(useJaxStore.getState().killSwitchActive).toBe(true)
  })

  it('reanudarKillSwitch con otro error no toca el estado', async () => {
    useJaxStore.setState({ killSwitchActive: true })
    const err = { response: { status: 503, data: { detail: 'kill_switch_no_escribible' } } }
    api.post.mockRejectedValue(err)
    await expect(useJaxStore.getState().reanudarKillSwitch()).rejects.toBe(err)
    expect(useJaxStore.getState().killSwitchActive).toBe(true)
  })

  it('activateKillSwitch ya no existe', () => {
    expect(useJaxStore.getState().activateKillSwitch).toBeUndefined()
  })
})
