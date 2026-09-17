import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createWebSocket } from './websocket'

// El backend cierra con 4001 cuando la sesión no vale: token viejo (sesión
// única: un login en otro lugar la mató), usuario inactivo, o la marca de
// cambio obligatorio (U34). Reintentar con el MISMO token daría otro 4001:
// un bucle que martilla el handshake. Con 4001 el socket se queda cerrado; el
// token nuevo (useWebSocket depende de `token`) crea un socket nuevo.
class FakeWS {
  static OPEN = 1
  static instancias = []
  constructor(url) {
    this.url = url
    FakeWS.instancias.push(this)
  }
  send() {}
  close() {}
}

beforeEach(() => {
  FakeWS.instancias = []
  vi.stubGlobal('WebSocket', FakeWS)
  vi.useFakeTimers()
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('createWebSocket -- reconexión', () => {
  it('un cierre 4001 no reconecta nunca', () => {
    const onStatus = vi.fn()
    createWebSocket('5', 'tok', vi.fn(), onStatus)
    FakeWS.instancias[0].onclose({ code: 4001 })
    vi.advanceTimersByTime(120000)
    expect(FakeWS.instancias).toHaveLength(1)
    expect(onStatus).toHaveBeenLastCalledWith('disconnected')
  })

  it('un corte de red (1006) sí reconecta con backoff', () => {
    const onStatus = vi.fn()
    createWebSocket('5', 'tok', vi.fn(), onStatus)
    FakeWS.instancias[0].onclose({ code: 1006 })
    expect(onStatus).toHaveBeenLastCalledWith('reconnecting')
    vi.advanceTimersByTime(1000)
    expect(FakeWS.instancias).toHaveLength(2)
  })
})

// Revisión final, 8a: un mensaje que ya estaba encolado cuando el llamador
// cerró el socket (desmontaje, cambio de token) no dispara nada: ni
// 'connected' (que en useWebSocket recarga el estado de una sesión que se
// fue) ni eventos.
describe('createWebSocket -- después de close()', () => {
  it('un auth_ok encolado no marca connected', () => {
    const onStatus = vi.fn()
    const conexion = createWebSocket('5', 'tok', vi.fn(), onStatus)
    conexion.close()
    FakeWS.instancias[0].onmessage({ data: JSON.stringify({ type: 'auth_ok' }) })
    expect(onStatus).not.toHaveBeenCalledWith('connected')
  })

  it('un evento encolado de una conexión autenticada no llega al llamador', () => {
    const onMessage = vi.fn()
    const conexion = createWebSocket('5', 'tok', onMessage, vi.fn())
    FakeWS.instancias[0].onmessage({ data: JSON.stringify({ type: 'auth_ok' }) })
    conexion.close()
    FakeWS.instancias[0].onmessage({ data: JSON.stringify({ event_type: 'facet_status_changed' }) })
    expect(onMessage).not.toHaveBeenCalled()
  })
})

// P10/ítem (e): el catch de onmessage se tragaba un JSON inválido sin dejar
// rastro. No puede tirar el socket, pero tampoco puede desaparecer en
// silencio.
describe('createWebSocket -- mensaje malformado', () => {
  it('un JSON inválido no llega al llamador y queda rastro en consola', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const onMessage = vi.fn()
    createWebSocket('5', 'tok', onMessage, vi.fn())
    FakeWS.instancias[0].onmessage({ data: '{no es json' })
    expect(onMessage).not.toHaveBeenCalled()
    expect(spy).toHaveBeenCalled()
    spy.mockRestore()
  })
})
