import axios from 'axios'
import { useJaxStore } from '../store/useJaxStore'
import { codigoDe } from './errores'

// Código que manda el backend (auth/middleware.py, SESION_INVALIDA) cuando
// el 401 es por usuario desactivado/borrado/degradado o versión de token
// vieja — no por simple vencimiento. Repetido acá como literal porque no hay
// forma de importar una constante de Python: si el backend lo cambia algún
// día, el contrato es el propio string "sesion_invalida" en el JSON.
const SESION_INVALIDA = 'sesion_invalida'

// I-1 (revisión final, 2026-09-14): un 401 de estos endpoints NO es "la
// sesión se cayó a mitad de camino" -- es la propia respuesta de auth. Antes
// el interceptor los trataba igual que cualquier otro 401: un visitante sin
// cookie (restoreSession -> /auth/refresh -> 401) disparaba un SEGUNDO
// refresh que también fallaba, y terminaba con avisoSesion = 'sesion_expirada'
// sin haber tenido nunca una sesión; una contraseña equivocada (/auth/login
// -> 401) hacía lo mismo y sumaba una segunda caja roja encima del error de
// credenciales. Estos tres nunca deben reintentar ni tocar el store acá.
const ENDPOINTS_DE_AUTH_SIN_REINTENTO = ['/auth/login', '/auth/refresh', '/auth/logout']

const api = axios.create({
  baseURL: '/api',
  withCredentials: true,
})

api.interceptors.request.use((config) => {
  const token = useJaxStore.getState().token
  if (token && !config.headers.Authorization) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (res) => res,
  async (err) => {
    const esAuthSinReintento = ENDPOINTS_DE_AUTH_SIN_REINTENTO.includes(err.config?.url)
    if (err.response?.status === 401 && !err.config?._retried && !esAuthSinReintento) {
      try {
        const { data } = await axios.post('/api/auth/refresh', {}, { withCredentials: true })
        useJaxStore.setState({ token: data.access_token })
        err.config._retried = true
        err.config.headers.Authorization = `Bearer ${data.access_token}`
        return axios(err.config)
      } catch (refreshErr) {
        // Nunca se borra la sesión en silencio: se deja el motivo ANTES de
        // borrar token/user. 'sesion_invalida' sólo cuando el backend lo dijo
        // explícito -- en el 401 original o en el refresh fallido -- porque
        // es la única causa que amerita un mensaje distinto ("te desactivaron
        // / te cambiaron el acceso") de un simple vencimiento por tiempo.
        const avisoSesion =
          codigoDe(refreshErr) === SESION_INVALIDA || codigoDe(err) === SESION_INVALIDA
            ? 'sesion_invalida'
            : 'sesion_expirada'
        useJaxStore.setState({ token: null, user: null, avisoSesion })
      }
    }
    return Promise.reject(err)
  }
)

export default api
