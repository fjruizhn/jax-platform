import { describe, it, expect, vi, beforeEach } from 'vitest'

// I-1 (revisión final, 2026-09-14): el interceptor de 401 trataba TODOS los
// 401 igual, incluidos los de /auth/login y /auth/refresh -- así que:
//   - restoreSession() (App.jsx, sin cookie) pedía /auth/refresh, recibía
//     401, y el interceptor intentaba OTRO refresh -- dos llamadas, y al
//     fallar ponía avisoSesion = 'sesion_expirada' en un visitante que nunca
//     tuvo sesión.
//   - Un login con contraseña equivocada (/auth/login -> 401) disparaba el
//     mismo camino: dos cajas rojas ("Tu sesión venció" + "credenciales
//     incorrectas") para alguien que sólo se equivocó de clave.
// Ahora el interceptor no interviene (ni reintenta, ni toca el store) cuando
// el request que falló es uno de los propios endpoints de auth.
//
// A diferencia de client.test.js (que mockea useJaxStore entero), acá se usa
// el store REAL -- integración de verdad entre el interceptor y el store.
// Sólo se mockea 'axios' (para no hacer llamadas de red reales), igual que
// en client.test.js.
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

let onRejected
let useJaxStore

beforeEach(async () => {
  vi.resetModules()
  axiosPostMock.mockReset()
  requestUse.mockClear()
  responseUse.mockClear()
  // Importar el store real dispara, como efecto de módulo, el import de
  // api/client.js (useJaxStore.js hace `import api from '../api/client'`) --
  // así que para cuando termina este import, el interceptor ya se registró.
  ;({ useJaxStore } = await import('../store/useJaxStore'))
  useJaxStore.setState({ token: 'viejo', user: { user_id: 1 }, avisoSesion: null })
  onRejected = responseUse.mock.calls[0][1]
})

const err401 = (url, detail) => ({
  response: { status: 401, data: detail === undefined ? {} : { detail } },
  config: { headers: {}, url },
})

describe('client.js -- el interceptor no toca los endpoints de auth (integración con el store real)', () => {
  it('(a) 401 de /auth/refresh (restoreSession sin cookie) no reintenta y no pone aviso', async () => {
    await expect(onRejected(err401('/auth/refresh'))).rejects.toBeTruthy()

    // Una sola llamada a refresh en todo el flujo es la que hizo
    // restoreSession() -- el interceptor no debe sumar una segunda.
    expect(axiosPostMock).not.toHaveBeenCalled()
    expect(useJaxStore.getState().avisoSesion).toBeNull()
  })

  it('(b) 401 de /auth/login no pone aviso', async () => {
    await expect(onRejected(err401('/auth/login'))).rejects.toBeTruthy()

    expect(axiosPostMock).not.toHaveBeenCalled()
    expect(useJaxStore.getState().avisoSesion).toBeNull()
  })

  it('/auth/logout tampoco dispara el reintento ni el aviso', async () => {
    await expect(onRejected(err401('/auth/logout'))).rejects.toBeTruthy()

    expect(axiosPostMock).not.toHaveBeenCalled()
    expect(useJaxStore.getState().avisoSesion).toBeNull()
  })

  it('(c) CONTROL: 401 de otra ruta con refresh fallido sigue poniendo el aviso', async () => {
    axiosPostMock.mockRejectedValue({ response: { status: 401, data: {} } })

    await expect(onRejected(err401('/pipelines/x'))).rejects.toBeTruthy()

    expect(axiosPostMock).toHaveBeenCalledTimes(1)
    expect(useJaxStore.getState().avisoSesion).toBe('sesion_expirada')
    expect(useJaxStore.getState().token).toBeNull()
  })

  it('CONTROL: 401 de otra ruta con refresh exitoso reintenta y no toca el aviso', async () => {
    axiosPostMock.mockResolvedValue({ data: { access_token: 'nuevo' } })

    const result = await onRejected(err401('/pipelines/x'))

    expect(result).toEqual({ data: 'retried' })
    expect(useJaxStore.getState().avisoSesion).toBeNull()
    expect(useJaxStore.getState().token).toBe('nuevo')
  })
})
