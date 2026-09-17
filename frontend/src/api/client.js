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

// Minor 5 del review final de la etapa 4 (2026-09-15, Ruling U27): el cambio de
// la propia contraseña sube token_version. Un 401 de un poll que salió con el
// access viejo mientras el cambio está en vuelo NO debe ir a /auth/refresh: el
// navegador puede no haber aplicado todavía la cookie de refresh nueva, el
// refresh falla y la pestaña que cambió la contraseña queda deslogueada. Se
// espera la promesa del cambio (store.cambioDePasswordEnCurso) y se reintenta
// una vez con el token nuevo. Nunca para el propio POST: esperaría su propia
// promesa y no terminaría nunca.
const CAMBIO_DE_PASSWORD = '/auth/me/password'

async function tokenTrasCambioDePassword(config) {
  const cambio = useJaxStore.getState().cambioDePasswordEnCurso
  if (!cambio || config?.url === CAMBIO_DE_PASSWORD) return null
  try {
    await cambio
  } catch {
    return null // el cambio falló: camino normal del refresh
  }
  return useJaxStore.getState().token
}

// U34 (2026-09-15): el admin fijó la contraseña. La sesión es válida, pero el
// backend niega todo salvo /me, /me/password, /refresh y /logout con este 403.
const CAMBIO_REQUERIDO = 'cambio_de_password_requerido'

// Kill switch (2026-09-16, frente B): chat, imagen, comando y pipelines
// responden 423 con este código cuando el freno está puesto.
const KILL_SWITCH_ACTIVO = 'kill_switch_activo'

const api = axios.create({
  baseURL: '/api',
  withCredentials: true,
})

api.interceptors.request.use((config) => {
  const token = useJaxStore.getState().token
  if (token && !config.headers.Authorization) {
    config.headers.Authorization = `Bearer ${token}`
  }
  // Época de sesión con la que salió el pedido (fix round 1 del review de
  // dd47d82): un 401 de un pedido de una sesión que ya terminó no es un aviso.
  config._epoch = useJaxStore.getState()._sessionEpoch
  return config
})

api.interceptors.response.use(
  (res) => res,
  async (err) => {
    // Kill switch (2026-09-16, frente B): cualquier pedido frenado enciende el
    // aviso, aunque el WS se haya perdido el evento. Sin reintento.
    if (err.response?.status === 423 && codigoDe(err) === KILL_SWITCH_ACTIVO) {
      useJaxStore.setState({ killSwitchActive: true })
      return Promise.reject(err)
    }
    // U34: sin refresh ni reintento (el token vale; reintentar daría otro 403).
    // Se prende la marca y RequireAuth desmonta la app y muestra el cambio
    // obligatorio: ya no sale ningún pedido más, así que no hay bucle.
    if (err.response?.status === 403 && codigoDe(err) === CAMBIO_REQUERIDO) {
      const { user } = useJaxStore.getState()
      if (user && !user.must_change_password) useJaxStore.setState({ user: { ...user, must_change_password: true } })
      return Promise.reject(err)
    }
    const esAuthSinReintento = ENDPOINTS_DE_AUTH_SIN_REINTENTO.includes(err.config?.url)
    // Fix round 1 (review de dd47d82): un 401 durante un logout voluntario (en
    // vuelo) o de un pedido de una época de sesión ya cerrada no es "la sesión
    // se cerró en otro lugar": sin refresh y sin aviso. Un login más nuevo en
    // OTRO lado no cambia la época de esta pestaña: ese 401 sigue al refresh y
    // al aviso sesion_invalida.
    if (err.response?.status === 401) {
      const { saliendo, _sessionEpoch } = useJaxStore.getState()
      const epocaVieja = err.config?._epoch !== undefined && err.config._epoch !== _sessionEpoch
      if (saliendo || epocaVieja) return Promise.reject(err)
    }
    if (err.response?.status === 401 && !err.config?._retried && !esAuthSinReintento) {
      const tokenNuevo = await tokenTrasCambioDePassword(err.config)
      if (tokenNuevo) {
        err.config._retried = true
        err.config.headers.Authorization = `Bearer ${tokenNuevo}`
        return axios(err.config)
      }
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
        // Frente C (2026-09-17): un 5xx del refresh no es una sesión vencida
        // (p.ej. 503 ajuste_ilegible: la vida de la sesión no se puede leer).
        // Se dice que es el servidor; el 401 sigue igual.
        const avisoSesion =
          codigoDe(refreshErr) === SESION_INVALIDA || codigoDe(err) === SESION_INVALIDA
            ? 'sesion_invalida'
            : codigoDe(refreshErr) === 'ajuste_ilegible'
              ? 'ajuste_ilegible'
              : refreshErr?.response?.status >= 500
                ? 'error_del_servidor'
                : 'sesion_expirada'
        useJaxStore.setState({ token: null, user: null, avisoSesion })
      }
    }
    return Promise.reject(err)
  }
)

export default api
