import { useRef, useState } from 'react'
import { useI18n } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
import { codigoDe } from '../api/errores'
import { officeSessionApi } from '../api/virtualOffice'
import PasswordInput from '../components/PasswordInput'
import AlertaError from '../components/AlertaError'
import { TAMANO_BOTON_ACCION } from '../tema/botones'

const INPUT = 'w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco'

function respuestaActiva(data, userId) {
  return data?.state === 'ACTIVE'
    && data?.target_user_id === userId
    && data?.verification_method === 'PASSWORD_REAUTH'
    && typeof data?.access_token === 'string'
    && data.access_token.length > 0
}

export default function OfficeBootstrap({ officeSession }) {
  const { t } = useI18n()
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const generation = useRef(0)

  async function submit(event) {
    event.preventDefault()
    setError('')
    setLoading(true)
    const request = ++generation.current
    const snapshot = useJaxStore.getState()
    const userId = officeSession?.identity?.user_id
    try {
      const { data } = await officeSessionApi.bootstrap({ current_password: password })
      if (request !== generation.current) return
      if (!respuestaActiva(data, userId)) throw new Error('invalid office bootstrap response')
      const current = useJaxStore.getState()
      if (current._sessionEpoch !== snapshot._sessionEpoch || current.token !== snapshot.token || current.user?.user_id !== snapshot.user?.user_id) return
      setPassword('')
      // La promoción rota la sesión anterior y devuelve un bearer nuevo. Al
      // cambiar también la época, ninguna respuesta de la sesión vieja puede
      // volver a pintar datos; RequireOfficeSession consulta de nuevo y abre
      // la Oficina con la autorización recién emitida.
      useJaxStore.setState((current) => ({ token: data.access_token, _sessionEpoch: current._sessionEpoch + 1 }))
    } catch (err) {
      if (request !== generation.current) return
      setPassword('')
      if (codigoDe(err) === 'bootstrap_reauth_failed') setError(t.virtualOffice.bootstrap.wrongPassword)
      else if (err?.response?.status === 503) setError(t.virtualOffice.bootstrap.unavailable)
      else setError(t.virtualOffice.bootstrap.error)
    } finally {
      if (request === generation.current) setLoading(false)
    }
  }

  return (
    <main data-foco-inicial tabIndex={-1} className="min-h-dvh bg-fondo p-4 text-texto">
      <section className="mx-auto max-w-md space-y-4 rounded-xl border border-borde bg-superficie p-6">
        <p className="text-xs font-semibold uppercase tracking-widest text-acento-texto">{t.virtualOffice.kicker}</p>
        <h1 className="text-xl font-bold">{t.virtualOffice.bootstrap.title}</h1>
        <p className="text-sm text-texto-tenue">{t.virtualOffice.bootstrap.intro}</p>
        <form onSubmit={submit} className="space-y-4">
          <label htmlFor="office-bootstrap-password" className="block text-xs font-semibold uppercase tracking-wider text-texto-suave">
            {t.virtualOffice.bootstrap.password}
          </label>
          <PasswordInput id="office-bootstrap-password" value={password} onChange={(event) => setPassword(event.target.value)} className={INPUT} autoComplete="current-password" required autoFocus />
          {error && <AlertaError className="rounded-lg border border-peligro-borde bg-peligro-fondo px-3 py-2 text-sm">{error}</AlertaError>}
          <button disabled={loading} className={`${TAMANO_BOTON_ACCION} w-full rounded bg-accion text-sobre-color disabled:opacity-50`}>
            {loading ? t.virtualOffice.bootstrap.activating : t.virtualOffice.bootstrap.activate}
          </button>
        </form>
      </section>
    </main>
  )
}
