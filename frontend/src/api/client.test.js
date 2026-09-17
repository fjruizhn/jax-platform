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

  // Frente C, revisión final (2026-09-17): un 5xx del refresh (p.ej. 503
  // ajuste_ilegible) no es una sesión vencida: decirlo manda a la persona a
  // reloguearse y el login vuelve a fallar igual.
  it('refresh falla con 503 ajuste_ilegible -> avisoSesion ajuste_ilegible, no sesion_expirada', async () => {
    axiosPostMock.mockRejectedValue({
      response: { status: 503, data: { detail: { code: 'ajuste_ilegible', clave: 'session_lifetime_seconds' } } },
    })

    await expect(onRejected(err401())).rejects.toBeTruthy()

    expect(setStateMock).toHaveBeenCalledWith({ token: null, user: null, avisoSesion: 'ajuste_ilegible' })
  })

  it('refresh falla con otro 5xx -> avisoSesion error_del_servidor', async () => {
    axiosPostMock.mockRejectedValue({ response: { status: 502, data: {} } })

    await expect(onRejected(err401())).rejects.toBeTruthy()

    expect(setStateMock).toHaveBeenCalledWith({ token: null, user: null, avisoSesion: 'error_del_servidor' })
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

// U34 (2026-09-15): el admin fijó la contraseña. La sesión es válida pero el
// backend niega todo salvo /me, /me/password, /refresh y /logout con 403
// cambio_de_password_requerido. El interceptor prende la marca (RequireAuth
// muestra el cambio obligatorio) y deja seguir el error: sin refresh, sin
// reintento, sin bucle.
describe('client.js -- 403 cambio_de_password_requerido (U34)', () => {
  const err403 = (detail) => ({ response: { status: 403, data: { detail } }, config: { headers: {} } })

  it('prende la marca en el usuario del store, sin refresh ni reintento', async () => {
    getStateMock.mockReturnValue({ token: 't', user: { user_id: 5 } })
    await expect(onRejected(err403('cambio_de_password_requerido'))).rejects.toBeTruthy()
    expect(setStateMock).toHaveBeenCalledTimes(1)
    expect(setStateMock).toHaveBeenCalledWith({ user: { user_id: 5, must_change_password: true } })
    expect(axiosPostMock).not.toHaveBeenCalled()
  })

  it('otro 403 no toca el store', async () => {
    getStateMock.mockReturnValue({ token: 't', user: { user_id: 5 } })
    await expect(onRejected(err403('Solo superadmin'))).rejects.toBeTruthy()
    expect(setStateMock).not.toHaveBeenCalled()
  })
})

// Fix round 1 (review de dd47d82): un 401 durante o después de un logout
// VOLUNTARIO no es "la sesión se cerró en otro lugar": sin refresh y sin aviso.
// El caso de un login más nuevo en otro lado (misma época, sin logout en
// vuelo) sigue mostrando el aviso: lo cubre 'client.js -- aviso de por qué se
// cerró la sesión'.
describe('client.js -- 401 durante o después de un logout voluntario', () => {
  it('cada pedido lleva la época de sesión con la que salió', () => {
    getStateMock.mockReturnValue({ token: 't', _sessionEpoch: 7 })
    const onRequest = requestUse.mock.calls[0][0]
    expect(onRequest({ headers: {} })._epoch).toBe(7)
  })

  it('con el logout en vuelo: sin refresh ni aviso', async () => {
    getStateMock.mockReturnValue({ token: 't', _sessionEpoch: 3, saliendo: new Promise(() => {}) })
    await expect(onRejected({ ...err401('sesion_invalida'), config: { headers: {}, _epoch: 3 } })).rejects.toBeTruthy()
    expect(axiosPostMock).not.toHaveBeenCalled()
    expect(setStateMock).not.toHaveBeenCalled()
  })

  it('con la época ya cambiada (el logout terminó): sin refresh ni aviso', async () => {
    getStateMock.mockReturnValue({ token: null, _sessionEpoch: 4, saliendo: null })
    await expect(onRejected({ ...err401('sesion_invalida'), config: { headers: {}, _epoch: 3 } })).rejects.toBeTruthy()
    expect(axiosPostMock).not.toHaveBeenCalled()
    expect(setStateMock).not.toHaveBeenCalled()
  })
})
