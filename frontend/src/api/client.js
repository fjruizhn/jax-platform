import axios from 'axios'
import { useJaxStore } from '../store/useJaxStore'
import { codigoDe } from './errores'

// Código que manda el backend (auth/middleware.py, SESION_INVALIDA) cuando
// el 401 es por usuario desactivado/borrado/degradado o versión de token
// vieja — no por simple vencimiento. Repetido acá como literal porque no hay
// forma de importar una constante de Python: si el backend lo cambia algún
// día, el contrato es el propio string "sesion_invalida" en el JSON.
const SESION_INVALIDA = 'sesion_invalida'

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
    if (err.response?.status === 401 && !err.config?._retried) {
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
