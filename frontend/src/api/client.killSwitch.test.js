import { describe, it, expect, vi, beforeEach } from 'vitest'

const requestUse = vi.fn()
const responseUse = vi.fn()

vi.mock('axios', () => {
  const instance = { interceptors: { request: { use: requestUse }, response: { use: responseUse } } }
  const axiosFn = vi.fn(() => Promise.resolve({ data: 'retried' }))
  axiosFn.create = vi.fn(() => instance)
  axiosFn.post = vi.fn()
  return { default: axiosFn }
})

const setStateMock = vi.fn()
vi.mock('../store/useJaxStore', () => ({
  useJaxStore: { getState: vi.fn(() => ({ token: 't', user: { user_id: '7' } })), setState: setStateMock },
}))

import { textoDeErrorDeMesa } from './errores'
import es from '../i18n/es.js'

let onRejected

beforeEach(async () => {
  vi.resetModules()
  setStateMock.mockReset()
  responseUse.mockClear()
  await import('./client')
  onRejected = responseUse.mock.calls[0][1]
})

describe('interceptor -- 423 del kill switch', () => {
  it('enciende el aviso en el store y rechaza igual', async () => {
    const err = { response: { status: 423, data: { detail: 'kill_switch_activo' } }, config: { url: '/chat' } }
    await expect(onRejected(err)).rejects.toBe(err)
    expect(setStateMock).toHaveBeenCalledWith({ killSwitchActive: true })
  })

  it('un 423 de otra cosa no toca el freno', async () => {
    const err = { response: { status: 423, data: { detail: 'cuenta_bloqueada' } }, config: { url: '/auth/login' } }
    await expect(onRejected(err)).rejects.toBe(err)
    expect(setStateMock).not.toHaveBeenCalledWith({ killSwitchActive: true })
  })
})

// Ruling R4 (2026-09-17): el 423 se traduce con el mismo camino que los demás
// errores de la Mesa (textoDeErrorDeMesa, A-51); no hay función aparte.
describe('textoDeErrorDeMesa -- 423 del kill switch', () => {
  it('traduce el código y no deja pasar el crudo', () => {
    const err423 = { response: { status: 423, data: { detail: 'kill_switch_activo' } } }
    expect(textoDeErrorDeMesa(es, err423, 'x')).toBe(es.erroresMesa.kill_switch_activo())
  })
})
