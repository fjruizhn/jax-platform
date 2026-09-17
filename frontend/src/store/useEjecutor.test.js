import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({ default: { post: vi.fn(), get: vi.fn() } }))

import api from '../api/client'
import { useJaxStore } from './useJaxStore'
import { useEjecutor, INTERVALO_POLLING_MS, LIMITE_MISIONES } from './useEjecutor'

// Store del modo Ejecutor (SP2, 2026-09-17). Contrato: contrato-api-ejecutor.md.
const JAX_INICIAL = useJaxStore.getState()
const INICIAL = useEjecutor.getState()

function mision(estado, extra = {}) {
  return { id: 'm1', objetivo: 'ver memoria', maquinas: ['ejecutor-prueba'], estado, puede_continuar: false, turnos: [], ...extra }
}

// GET por ruta: detalle y bitácora de la misión m1.
function responderGet(detalle) {
  api.get.mockImplementation((url) => {
    if (url === '/ejecutor/misiones/m1') return Promise.resolve({ data: detalle() })
    if (url === '/ejecutor/misiones/m1/bitacora') return Promise.resolve({ data: { eventos: [{ id: 1, turno: 1, evento: 'mision_creada', datos: {}, at: '2026-09-17T10:00:00Z' }] } })
    if (url === '/ejecutor/misiones') return Promise.resolve({ data: { misiones: [] } })
    if (url === '/ejecutor/estado') return Promise.resolve({ data: { pausa: { puesta: false, legible: true }, maquinas: [], compuerta_datos_de_clientes: 'cerrada', turno_en_curso: null } })
    return Promise.reject(new Error(`GET inesperado ${url}`))
  })
}

const llamadasDetalle = () => api.get.mock.calls.filter(([u]) => u === '/ejecutor/misiones/m1').length

beforeEach(() => {
  useJaxStore.setState({ ...JAX_INICIAL, token: 'tok', user: { user_id: '1', role: 'superadmin' } }, true)
  useEjecutor.getState().reiniciar()
  useEjecutor.setState(INICIAL, true)
  vi.clearAllMocks()
})

afterEach(() => {
  useEjecutor.getState().detenerPolling()
  vi.useRealTimers()
})

describe('useEjecutor -- estado y misiones', () => {
  it('cargarEstado guarda /estado y cargarMisiones pide el límite con nombre', async () => {
    responderGet(() => mision('completada'))
    await useEjecutor.getState().cargarEstado()
    await useEjecutor.getState().cargarMisiones()
    expect(useEjecutor.getState().estado.compuerta_datos_de_clientes).toBe('cerrada')
    expect(api.get).toHaveBeenCalledWith('/ejecutor/misiones', { params: { limite: LIMITE_MISIONES } })
  })

  it('un error de /estado queda guardado, no se traga', async () => {
    const err = { response: { status: 503, data: { detail: 'ejecutor_sin_configurar' } } }
    api.get.mockRejectedValue(err)
    await useEjecutor.getState().cargarEstado()
    expect(useEjecutor.getState().errorEstado).toBe(err)
  })

  it('alternarMaquina agrega y quita de la selección', () => {
    useEjecutor.getState().alternarMaquina('a')
    useEjecutor.getState().alternarMaquina('b')
    useEjecutor.getState().alternarMaquina('a')
    expect(useEjecutor.getState().seleccion).toEqual(['b'])
  })
})

describe('useEjecutor -- enviar', () => {
  it('sin misión abierta crea una misión con las máquinas elegidas', async () => {
    responderGet(() => mision('completada'))
    api.post.mockResolvedValue({ data: mision('completada') })
    useEjecutor.setState({ seleccion: ['ejecutor-prueba'] })
    expect(await useEjecutor.getState().enviar('ver memoria')).toBe(true)
    expect(api.post).toHaveBeenCalledWith('/ejecutor/misiones', { objetivo: 'ver memoria', maquinas: ['ejecutor-prueba'] })
    expect(useEjecutor.getState().misionActiva.id).toBe('m1')
  })

  it('con una misión que puede continuar, manda el turno siguiente', async () => {
    responderGet(() => mision('completada', { puede_continuar: true }))
    useEjecutor.setState({ misionActiva: mision('completada', { puede_continuar: true }) })
    api.post.mockResolvedValue({ data: mision('completada', { puede_continuar: true }) })
    await useEjecutor.getState().enviar('ahora el disco')
    expect(api.post).toHaveBeenCalledWith('/ejecutor/misiones/m1/turnos', { instruccion: 'ahora el disco' })
  })

  it('con una misión que NO puede continuar, crea una nueva', async () => {
    responderGet(() => mision('completada'))
    useEjecutor.setState({ misionActiva: mision('fallida', { puede_continuar: false }) })
    api.post.mockResolvedValue({ data: mision('completada') })
    await useEjecutor.getState().enviar('otra cosa')
    expect(api.post.mock.calls[0][0]).toBe('/ejecutor/misiones')
  })

  it('un error del POST queda en errorEnvio y enviar devuelve false', async () => {
    const err = { response: { status: 423, data: { detail: 'ejecutor_pausado' } } }
    api.post.mockRejectedValue(err)
    expect(await useEjecutor.getState().enviar('x')).toBe(false)
    expect(useEjecutor.getState().errorEnvio).toBe(err)
  })
})

describe('useEjecutor -- polling mientras en_curso', () => {
  it('corre mientras la misión está en_curso y se detiene al terminar', async () => {
    vi.useFakeTimers()
    let estado = 'en_curso'
    responderGet(() => mision(estado))
    await useEjecutor.getState().abrirMision('m1')
    expect(useEjecutor.getState().misionActiva.estado).toBe('en_curso')
    expect(useEjecutor.getState().bitacora).toHaveLength(1)
    const antes = llamadasDetalle()
    await vi.advanceTimersByTimeAsync(INTERVALO_POLLING_MS)
    expect(llamadasDetalle()).toBe(antes + 1)
    estado = 'completada'
    await vi.advanceTimersByTimeAsync(INTERVALO_POLLING_MS)
    expect(useEjecutor.getState().misionActiva.estado).toBe('completada')
    const alTerminar = llamadasDetalle()
    await vi.advanceTimersByTimeAsync(INTERVALO_POLLING_MS * 5)
    expect(llamadasDetalle()).toBe(alTerminar)
  })

  it('una misión terminada no arranca polling', async () => {
    vi.useFakeTimers()
    responderGet(() => mision('completada'))
    await useEjecutor.getState().abrirMision('m1')
    const n = llamadasDetalle()
    await vi.advanceTimersByTimeAsync(INTERVALO_POLLING_MS * 3)
    expect(llamadasDetalle()).toBe(n)
  })

  it('detenerPolling (desmontar) lo corta', async () => {
    vi.useFakeTimers()
    responderGet(() => mision('en_curso'))
    await useEjecutor.getState().abrirMision('m1')
    useEjecutor.getState().detenerPolling()
    const n = llamadasDetalle()
    await vi.advanceTimersByTimeAsync(INTERVALO_POLLING_MS * 3)
    expect(llamadasDetalle()).toBe(n)
  })

  it('cerrar sesión (cambia la época) corta el polling y borra lo del Ejecutor', async () => {
    vi.useFakeTimers()
    responderGet(() => mision('en_curso'))
    await useEjecutor.getState().abrirMision('m1')
    useJaxStore.setState((s) => ({ _sessionEpoch: s._sessionEpoch + 1, token: null, user: null }))
    const n = llamadasDetalle()
    await vi.advanceTimersByTimeAsync(INTERVALO_POLLING_MS * 3)
    expect(llamadasDetalle()).toBe(n)
    expect(useEjecutor.getState().misionActiva).toBeNull()
    expect(useEjecutor.getState().activo).toBe(false)
  })

  it('una respuesta de una sesión anterior no se escribe', async () => {
    let soltar
    api.get.mockImplementation(() => new Promise((r) => { soltar = r }))
    const p = useEjecutor.getState().cargarEstado()
    useJaxStore.setState((s) => ({ _sessionEpoch: s._sessionEpoch + 1 }))
    soltar({ data: { pausa: {}, maquinas: [] } })
    await p
    expect(useEjecutor.getState().estado).toBeNull()
  })
})

describe('useEjecutor -- bitácora incremental (?desde=)', () => {
  it('el sondeo pide sólo lo nuevo, lo anexa sin duplicar, y otra misión trae la lista completa', async () => {
    vi.useFakeTimers()
    const ev = (id, mid = 'm1') => ({ id, turno: 1, evento: 'paso', datos: { mid }, at: '2026-09-17T10:00:00Z' })
    const pedidos = []
    api.get.mockImplementation((url, config) => {
      pedidos.push([url, config])
      if (url === '/ejecutor/misiones/m1' || url === '/ejecutor/misiones/m2') return Promise.resolve({ data: mision('en_curso', { id: url.split('/').pop() }) })
      if (url === '/ejecutor/misiones/m1/bitacora') {
        const desde = config?.params?.desde
        // Devuelve el 2 repetido a propósito: no se tiene que duplicar.
        return Promise.resolve({ data: { eventos: desde === undefined ? [ev(1), ev(2)] : [ev(2), ev(3)] } })
      }
      if (url === '/ejecutor/misiones/m2/bitacora') return Promise.resolve({ data: { eventos: [ev(10, 'm2')] } })
      return Promise.resolve({ data: {} })
    })
    await useEjecutor.getState().abrirMision('m1')
    expect(pedidos.find(([u]) => u === '/ejecutor/misiones/m1/bitacora')[1]?.params?.desde).toBeUndefined()
    await vi.advanceTimersByTimeAsync(INTERVALO_POLLING_MS)
    const incremental = pedidos.filter(([u]) => u === '/ejecutor/misiones/m1/bitacora').at(-1)
    expect(incremental[1]).toEqual({ params: { desde: 2 } })
    expect(useEjecutor.getState().bitacora.map((e) => e.id)).toEqual([1, 2, 3])
    await useEjecutor.getState().abrirMision('m2')
    expect(pedidos.filter(([u]) => u === '/ejecutor/misiones/m2/bitacora')[0][1]?.params?.desde).toBeUndefined()
    expect(useEjecutor.getState().bitacora.map((e) => e.id)).toEqual([10])
  })
})

describe('useEjecutor -- pausa del Ejecutor', () => {
  it('poner usa la ruta del Ejecutor y guarda la pausa devuelta', async () => {
    useEjecutor.setState({ estado: { pausa: { puesta: false, legible: true }, maquinas: [] } })
    api.post.mockResolvedValue({ data: { puesta: true, origen: 'plataforma', motivo: 'manual', paso: null, momento: null, legible: true } })
    await useEjecutor.getState().ponerPausa()
    expect(api.post).toHaveBeenCalledWith('/ejecutor/pausa/poner')
    expect(useEjecutor.getState().estado.pausa.puesta).toBe(true)
  })

  it('quitar con error relee /estado y propaga', async () => {
    const err = { response: { status: 500, data: { detail: 'ejecutor_pausa_auditoria_fallida' } } }
    api.post.mockRejectedValue(err)
    responderGet(() => mision('completada'))
    await expect(useEjecutor.getState().quitarPausa()).rejects.toBe(err)
    expect(api.post).toHaveBeenCalledWith('/ejecutor/pausa/quitar')
    expect(api.get).toHaveBeenCalledWith('/ejecutor/estado')
  })
})
