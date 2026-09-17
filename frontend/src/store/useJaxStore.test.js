import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  default: {
    post: vi.fn(),
    get: vi.fn(),
  },
}))

import api from '../api/client'
import { useJaxStore } from './useJaxStore'

const INITIAL_STATE = useJaxStore.getState()

describe('restoreSession', () => {
  beforeEach(() => {
    useJaxStore.setState(INITIAL_STATE, true)
    vi.clearAllMocks()
  })

  it('never reads the JWT or user out of localStorage', async () => {
    const getItemSpy = vi.spyOn(Storage.prototype, 'getItem')
    api.post.mockResolvedValue({ data: { access_token: 'tok-123' } })
    api.get.mockResolvedValue({ data: { id: 1, email: 'a@b.com' } })

    await useJaxStore.getState().restoreSession()

    expect(getItemSpy).not.toHaveBeenCalledWith('jax_token')
    expect(getItemSpy).not.toHaveBeenCalledWith('jax_user')
  })

  it('sets token and user on a successful refresh', async () => {
    api.post.mockResolvedValue({ data: { access_token: 'tok-123' } })
    api.get.mockResolvedValue({ data: { id: 1, email: 'a@b.com' } })

    await useJaxStore.getState().restoreSession()

    const state = useJaxStore.getState()
    expect(state.token).toBe('tok-123')
    expect(state.user).toEqual({ id: 1, email: 'a@b.com' })
    expect(state.sessionRestoring).toBe(false)
  })

  it('clears token and user when the refresh fails', async () => {
    api.post.mockRejectedValue(new Error('401'))

    await useJaxStore.getState().restoreSession()

    const state = useJaxStore.getState()
    expect(state.token).toBeNull()
    expect(state.user).toBeNull()
    expect(state.sessionRestoring).toBe(false)
  })
})

// Mi cuenta (2026-09-12, admin usuarios etapa 4): cambiar la propia
// contraseña reemplaza `token` con el access nuevo que manda el backend --
// las otras sesiones quedan cerradas por su propio token_version.
describe('cambiarMiPassword', () => {
  beforeEach(() => {
    useJaxStore.setState(INITIAL_STATE, true)
    vi.clearAllMocks()
  })

  it('postea current/new password y reemplaza el token con el access nuevo', async () => {
    api.post.mockResolvedValue({ data: { access_token: 'tok-nuevo' } })

    await useJaxStore.getState().cambiarMiPassword('vieja-clave', 'nueva-clave-9')

    expect(api.post).toHaveBeenCalledWith('/auth/me/password', {
      current_password: 'vieja-clave', new_password: 'nueva-clave-9',
    })
    expect(useJaxStore.getState().token).toBe('tok-nuevo')
  })

  // Minor 5 del review final (2026-09-15, Ruling U27): el interceptor de
  // api/client.js necesita saber que hay un cambio de contraseña en vuelo para
  // esperar el token nuevo en vez de llamar a /auth/refresh con la cookie
  // vieja. La promesa vive en el store mientras dura y se limpia al terminar.
  it('expone la promesa del cambio en curso y la limpia al terminar', async () => {
    let resolver
    api.post.mockReturnValue(new Promise((r) => { resolver = r }))

    const cambio = useJaxStore.getState().cambiarMiPassword('vieja-clave', 'nueva-clave-9')
    const enCurso = useJaxStore.getState().cambioDePasswordEnCurso
    expect(enCurso).toBeInstanceOf(Promise)

    resolver({ data: { access_token: 'tok-nuevo' } })
    await enCurso
    // Quien esperó la promesa ya ve el token nuevo en el store.
    expect(useJaxStore.getState().token).toBe('tok-nuevo')
    await cambio
    expect(useJaxStore.getState().cambioDePasswordEnCurso).toBeNull()
  })

  it('si el cambio falla, la promesa se limpia y el error sube', async () => {
    api.post.mockRejectedValue({ response: { status: 400, data: { detail: 'password_actual_incorrecta' } } })

    await expect(useJaxStore.getState().cambiarMiPassword('mala', 'nueva-clave-9')).rejects.toBeTruthy()
    expect(useJaxStore.getState().cambioDePasswordEnCurso).toBeNull()
    expect(useJaxStore.getState().token).toBeNull()
  })

  it('al terminar apaga must_change_password del usuario (U34)', async () => {
    useJaxStore.setState({ user: { user_id: 5, must_change_password: true } })
    api.post.mockResolvedValue({ data: { access_token: 'tok-nuevo' } })
    await useJaxStore.getState().cambiarMiPassword('fijada-por-admin', 'nueva-clave-9')
    expect(useJaxStore.getState().user).toEqual({ user_id: 5, must_change_password: false })
  })
})

// Sesión única (2026-09-15, Ruling F2): salir mata la sesión EN EL SERVIDOR
// (POST /auth/logout con la cookie de refresh). El pedido sale ANTES de
// limpiar el estado local: si no, un login rápido en la misma pestaña podía
// recibir su cookie nueva y después el delete_cookie de este logout.
describe('logout', () => {
  beforeEach(() => {
    useJaxStore.setState({ ...INITIAL_STATE, token: 'tok-vivo', user: { user_id: 5 } }, true)
    vi.clearAllMocks()
  })

  // Fix round 1 (review de ca8bb15): si el stub se restaura al final del
  // cuerpo del test, una aserción que tira ANTES de llegar ahí lo deja filtrado
  // a los tests siguientes. afterEach corre siempre, incluso con el test roto.
  afterEach(() => vi.unstubAllGlobals())

  it('llama a /auth/logout una vez, con la sesión todavía puesta, y después limpia', async () => {
    let tokenAlLlamar
    api.post.mockImplementation(() => {
      tokenAlLlamar = useJaxStore.getState().token
      return Promise.resolve({ data: { ok: true } })
    })
    await useJaxStore.getState().logout()
    expect(api.post).toHaveBeenCalledTimes(1)
    expect(api.post.mock.calls[0][0]).toBe('/auth/logout')
    expect(tokenAlLlamar).toBe('tok-vivo')
    expect(useJaxStore.getState().token).toBeNull()
    expect(useJaxStore.getState().user).toBeNull()
  })

  it('un doble clic envía un solo POST: la segunda llamada reusa la promesa en vuelo', async () => {
    let resolver
    api.post.mockReturnValue(new Promise((r) => { resolver = r }))
    const primera = useJaxStore.getState().logout()
    const segunda = useJaxStore.getState().logout()
    expect(segunda).toBe(primera)
    expect(useJaxStore.getState().saliendo).toBe(primera)
    resolver({ data: { ok: true } })
    await primera
    expect(api.post).toHaveBeenCalledTimes(1)
    expect(useJaxStore.getState().saliendo).toBeNull()
  })

  // Fix round 1 (review de dd47d82): un poll que llegó al servidor después del
  // logout daba 401 -> refresh fallido -> avisoSesion 'sesion_invalida' ("se
  // inició sesión en otro lugar") en un logout voluntario.
  it('borra avisoSesion: un logout voluntario no deja aviso en Login', async () => {
    useJaxStore.setState({ avisoSesion: 'sesion_invalida' })
    api.post.mockResolvedValue({ data: { ok: true } })
    await useJaxStore.getState().logout()
    expect(useJaxStore.getState().avisoSesion).toBeNull()
  })

  it('si el pedido falla, limpia igual y no reintenta', async () => {
    api.post.mockRejectedValue(new Error('red caída'))
    await useJaxStore.getState().logout()
    expect(api.post).toHaveBeenCalledTimes(1)
    expect(useJaxStore.getState().token).toBeNull()
    expect(useJaxStore.getState().user).toBeNull()
  })

  // RD4 (2026-09-17, Ruling R23-4): la miniatura de un adjunto de imagen en
  // el historial usa su propio object URL (adjuntos.js: vistaDeAdjunto).
  // logout() es el único hook de reseteo de sesión que vacía `messages` --
  // tiene que revocarlos ANTES de vaciar, o quedan colgados para siempre.
  it('revoca los object URL de las miniaturas del historial antes de vaciarlo', async () => {
    vi.stubGlobal('URL', { ...URL, revokeObjectURL: vi.fn() })
    useJaxStore.setState({
      messages: [
        { id: '1', facet: 'user', content: 'hola', attachment: { type: 'image', filename: 'f.png', base64: 'blob:abc' } },
        { id: '2', facet: 'user', content: 'otra', attachment: { type: 'image', filename: 'g.png', base64: 'blob:def' } },
        { id: '3', facet: 'user', content: 'texto', attachment: { type: 'text', filename: 'i.pdf' } },
        { id: '4', facet: 'thot', content: 'sin adjunto' },
      ],
    })
    api.post.mockResolvedValue({ data: { ok: true } })
    await useJaxStore.getState().logout()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:abc')
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:def')
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2)
    expect(useJaxStore.getState().messages).toEqual([])
  })
})

// Etapa 5 (2026-09-15, Task 4): el JWT no lleva el correo y la barra de
// usuario muestra user.email del store. Si el admin se edita SU PROPIO correo,
// la barra no puede quedar con el viejo hasta el próximo /auth/me.
describe('actualizarMiEmail', () => {
  beforeEach(() => {
    useJaxStore.setState(INITIAL_STATE, true)
    useJaxStore.setState({ user: { user_id: 1, email: 'viejo@x.io', role: 'superadmin' } })
  })

  it('editar el correo propio cambia user.email del store', () => {
    useJaxStore.getState().actualizarMiEmail(1, 'nuevo@x.io')
    expect(useJaxStore.getState().user).toEqual({ user_id: 1, email: 'nuevo@x.io', role: 'superadmin' })
  })

  it('editar el correo de otro usuario no toca user.email', () => {
    useJaxStore.getState().actualizarMiEmail(2, 'otro@x.io')
    expect(useJaxStore.getState().user.email).toBe('viejo@x.io')
  })
})
