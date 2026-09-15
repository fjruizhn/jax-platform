import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

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

// Minor 5 del review final de la etapa 4 (2026-09-15, Ruling U27): tras el
// commit de POST /auth/me/password el token_version sube; un poll que salió
// con el access viejo vuelve con 401. Si el interceptor llamaba a
// /auth/refresh antes de que el navegador aplicara la cookie de refresh nueva,
// el refresh fallaba y la pestaña que CAMBIÓ la contraseña quedaba deslogueada.
// Ahora, con un cambio en vuelo, se espera su promesa y se reintenta UNA vez con
// el token nuevo del store, sin /auth/refresh.
describe('client.js -- 401 durante un cambio de contraseña en vuelo', () => {
  let axiosFn
  beforeEach(async () => {
    axiosFn = (await import('axios')).default
    axiosFn.mockClear()
  })
  afterEach(() => {
    getStateMock.mockImplementation(() => ({ token: 'viejo' }))
  })

  it('espera el cambio y reintenta con el token nuevo, sin llamar a /auth/refresh', async () => {
    let resolver
    const cambio = new Promise((r) => { resolver = r })
    const estado = { token: 'viejo', cambioDePasswordEnCurso: cambio }
    getStateMock.mockImplementation(() => estado)

    const pendiente = onRejected({ ...err401(), config: { url: '/pipelines', headers: { Authorization: 'Bearer viejo' } } })
    // El store recibe el token nuevo cuando el cambio termina.
    estado.token = 'tok-nuevo'
    resolver()
    const result = await pendiente

    expect(result).toEqual({ data: 'retried' })
    expect(axiosPostMock).not.toHaveBeenCalled()
    expect(axiosFn).toHaveBeenCalledTimes(1)
    const reintento = axiosFn.mock.calls[0][0]
    expect(reintento.headers.Authorization).toBe('Bearer tok-nuevo')
    expect(reintento._retried).toBe(true)
    expect(setStateMock).not.toHaveBeenCalled()
  })

  it('si el cambio falla, cae al camino normal del refresh', async () => {
    const cambio = Promise.reject(new Error('400'))
    cambio.catch(() => {})
    getStateMock.mockImplementation(() => ({ token: 'viejo', cambioDePasswordEnCurso: cambio }))
    axiosPostMock.mockResolvedValue({ data: { access_token: 'refrescado' } })

    const result = await onRejected({ ...err401(), config: { url: '/pipelines', headers: {} } })

    expect(result).toEqual({ data: 'retried' })
    expect(axiosPostMock).toHaveBeenCalledWith('/api/auth/refresh', {}, { withCredentials: true })
    expect(axiosFn.mock.calls[0][0].headers.Authorization).toBe('Bearer refrescado')
  })

  it('un 401 del propio POST /auth/me/password no espera su propia promesa (sin deadlock)', async () => {
    getStateMock.mockImplementation(() => ({ token: 'viejo', cambioDePasswordEnCurso: new Promise(() => {}) }))
    axiosPostMock.mockResolvedValue({ data: { access_token: 'refrescado' } })

    const result = await onRejected({ ...err401(), config: { url: '/auth/me/password', headers: {} } })

    expect(result).toEqual({ data: 'retried' })
    expect(axiosPostMock).toHaveBeenCalledTimes(1)
  })

  it('un request ya reintentado no vuelve a esperar ni a reintentar (sin bucle)', async () => {
    getStateMock.mockImplementation(() => ({ token: 'viejo', cambioDePasswordEnCurso: Promise.resolve() }))

    const original = { ...err401(), config: { url: '/pipelines', headers: {}, _retried: true } }
    await expect(onRejected(original)).rejects.toBe(original)

    expect(axiosFn).not.toHaveBeenCalled()
    expect(axiosPostMock).not.toHaveBeenCalled()
  })

  it('sin cambio en vuelo, el camino del refresh no cambia', async () => {
    getStateMock.mockImplementation(() => ({ token: 'viejo', cambioDePasswordEnCurso: null }))
    axiosPostMock.mockResolvedValue({ data: { access_token: 'refrescado' } })

    const result = await onRejected({ ...err401(), config: { url: '/pipelines', headers: {} } })

    expect(result).toEqual({ data: 'retried' })
    expect(axiosPostMock).toHaveBeenCalledWith('/api/auth/refresh', {}, { withCredentials: true })
    expect(setStateMock).toHaveBeenCalledWith({ token: 'refrescado' })
  })
})
