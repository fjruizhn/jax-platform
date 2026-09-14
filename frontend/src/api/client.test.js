import { describe, it, expect, vi, beforeEach } from 'vitest'

// El interceptor de respuesta (2026-09-14, Task 4b): un 401 que sobrevive al
// refresh borraba la sesión EN SILENCIO (useJaxStore.setState({ token: null,
// user: null })) — la persona quedaba en Login sin saber por qué. Ahora deja
// el motivo en `avisoSesion` ANTES de borrar: 'sesion_invalida' cuando el
// backend lo dijo (en el 401 original o en el refresh fallido),
// 'sesion_expirada' en cualquier otro caso (p.ej. el refresh vence solo).
//
// axios.create() se llama en el módulo — hay que mockear 'axios' completo
// (default export con .create y .post) antes de importar client.js.
const axiosPostMock = vi.fn()
const requestUse = vi.fn()
const responseUse = vi.fn()

vi.mock('axios', () => {
  const instance = {
    interceptors: {
      request: { use: requestUse },
      response: { use: responseUse },
    },
  }
  const axiosFn = vi.fn(() => Promise.resolve({ data: 'retried' }))
  axiosFn.create = vi.fn(() => instance)
  axiosFn.post = axiosPostMock
  return { default: axiosFn }
})

const setStateMock = vi.fn()
const getStateMock = vi.fn(() => ({ token: 'viejo' }))
vi.mock('../store/useJaxStore', () => ({
  useJaxStore: { getState: getStateMock, setState: setStateMock },
}))

let onRejected

beforeEach(async () => {
  vi.resetModules()
  axiosPostMock.mockReset()
  setStateMock.mockReset()
  requestUse.mockClear()
  responseUse.mockClear()
  await import('./client')
  // El interceptor de respuesta es el segundo argumento del único use() de
  // interceptors.response — (onFulfilled, onRejected).
  onRejected = responseUse.mock.calls[0][1]
})

const err401 = (detail) => ({
  response: { status: 401, data: detail === undefined ? {} : { detail } },
  config: { headers: {} },
})

describe('client.js -- aviso de por qué se cerró la sesión', () => {
  it('refresh falla con detail sesion_invalida -> avisoSesion sesion_invalida y token null', async () => {
    axiosPostMock.mockRejectedValue({ response: { status: 401, data: { detail: 'sesion_invalida' } } })

    await expect(onRejected(err401())).rejects.toBeTruthy()

    expect(setStateMock).toHaveBeenCalledWith({
      token: null,
      user: null,
      avisoSesion: 'sesion_invalida',
    })
  })

  it('el 401 original ya traía sesion_invalida aunque el refresh no diga nada -> sesion_invalida', async () => {
    axiosPostMock.mockRejectedValue({ response: { status: 401, data: {} } })

    await expect(onRejected(err401('sesion_invalida'))).rejects.toBeTruthy()

    expect(setStateMock).toHaveBeenCalledWith({
      token: null,
      user: null,
      avisoSesion: 'sesion_invalida',
    })
  })

  it('refresh falla sin código -> avisoSesion sesion_expirada', async () => {
    axiosPostMock.mockRejectedValue({ response: { status: 401, data: {} } })

    await expect(onRejected(err401())).rejects.toBeTruthy()

    expect(setStateMock).toHaveBeenCalledWith({
      token: null,
      user: null,
      avisoSesion: 'sesion_expirada',
    })
  })

  it('refresh anda -> reintenta el request original y no toca avisoSesion', async () => {
    axiosPostMock.mockResolvedValue({ data: { access_token: 'nuevo' } })

    const result = await onRejected(err401())

    expect(result).toEqual({ data: 'retried' })
    expect(setStateMock).not.toHaveBeenCalledWith(
      expect.objectContaining({ avisoSesion: expect.anything() })
    )
  })
})
