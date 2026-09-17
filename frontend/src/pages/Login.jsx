import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useJaxStore } from '../store/useJaxStore'
import { useI18n } from '../i18n/index.jsx'
import { useNombreDelSistema } from '../store/useApariencia'
import api from '../api/client'
import PasswordInput from '../components/PasswordInput'
import AlertaError from '../components/AlertaError'
import HalEye from '../components/HalEye/HalEye'

export default function Login() {
  const login = useJaxStore((s) => s.login)
  const avisoSesion = useJaxStore((s) => s.avisoSesion)
  const clearAvisoSesion = useJaxStore((s) => s.clearAvisoSesion)
  const { lang, setLang, t } = useI18n()
  const nombre = useNombreDelSistema(t)
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  // Forgot password state
  const [showForgot, setShowForgot] = useState(false)
  const [forgotEmail, setForgotEmail] = useState('')
  const [forgotSending, setForgotSending] = useState(false)
  const [forgotSent, setForgotSent] = useState(false)
  const [forgotError, setForgotError] = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    // Se limpia al ENVIAR, no sólo al tener éxito (2026-09-14, I-1 de la
    // revisión final): si quedaba un aviso viejo (p.ej. "te desactivaron")
    // y esta persona escribe mal la contraseña, el aviso viejo ya no
    // convive con "Usuario o contraseña incorrectos" -- dos cajas rojas
    // para un solo intento.
    clearAvisoSesion()
    setLoading(true)
    try {
      await login(email, password)
      navigate('/')
    } catch (err) {
      const status = err.response?.status

      if (status === 423) {
        // A-50: segundos del backend (detail.retry_after_seconds), nunca texto.
        const segundos = err.response?.data?.detail?.retry_after_seconds
        setError(Number.isFinite(segundos) ? t.accountLockedMinutes(Math.ceil(segundos / 60)) : t.accountLocked)
      } else if (status === 429) {
        // Límite de intentos por IP/email (2026-09-12): no es un error de
        // credenciales, es "esperá". El backend manda Retry-After en segundos.
        const seconds = parseInt(err.response?.headers?.['retry-after'], 10)
        setError(Number.isFinite(seconds) ? t.tooManyAttemptsSeconds(seconds) : t.tooManyAttempts)
      } else {
        setError(t.loginError)
      }
    } finally {
      setLoading(false)
    }
  }

  async function handleForgot(e) {
    e.preventDefault()
    setForgotError('')
    setForgotSending(true)
    try {
      await api.post('/auth/forgot-password', { email: forgotEmail })
      setForgotSent(true)
    } catch (err) {
      // 429 (2026-09-12): la recuperación comparte el límite del login. Decir
      // "si el correo existe, te llegará" sería mentir: no se procesó nada.
      if (err.response?.status === 429) {
        const seconds = parseInt(err.response?.headers?.['retry-after'], 10)
        setForgotError(Number.isFinite(seconds) ? t.tooManyAttemptsSeconds(seconds) : t.tooManyAttempts)
      } else {
        setForgotSent(true)
      }
    } finally {
      setForgotSending(false)
    }
  }

  if (showForgot) {
    return (
      <div className="min-h-dvh bg-fondo flex items-center justify-center p-4">
        <div className="w-full max-w-md">
          <div className="flex justify-end gap-1 mb-4">
            {['es', 'en'].map((l) => (
              <button key={l} onClick={() => setLang(l)}
                className={`px-1.5 py-0.5 rounded text-xs font-bold uppercase transition-colors ${lang === l ? 'bg-accion text-sobre-color' : 'text-texto-tenue hover:text-texto'}`}>
                {l}
              </button>
            ))}
          </div>

          <h1 className="text-center text-2xl font-bold text-texto mb-1">{t.forgotPasswordTitle}</h1>
          <p className="text-center text-xs text-texto-tenue mb-8">{t.forgotPasswordDesc}</p>

          <div className="bg-superficie rounded-xl p-6 border border-borde space-y-4">
            {forgotSent ? (
              <div className="text-sm text-exito bg-exito-fondo border border-exito-borde rounded-lg px-3 py-3 text-center">
                {t.forgotPasswordSent}
              </div>
            ) : (
              <form onSubmit={handleForgot} className="space-y-4">
                <div>
                  <label className="block text-xs text-texto-suave mb-1 font-semibold uppercase tracking-wider">
                    {t.emailLabel}
                  </label>
                  <input
                    type="email"
                    value={forgotEmail}
                    onChange={(e) => setForgotEmail(e.target.value)}
                    className="w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco"
                    required
                    autoFocus
                  />
                </div>
                {forgotError && (
                  <div className="text-sm text-peligro bg-peligro-fondo border border-peligro-borde rounded-lg px-3 py-2">
                    {forgotError}
                  </div>
                )}
                <button
                  type="submit"
                  disabled={forgotSending}
                  className="w-full py-2.5 rounded-lg bg-accion hover:bg-accion-hover disabled:opacity-50 text-sobre-color font-semibold transition-colors"
                >
                  {forgotSending ? t.forgotPasswordSending : t.forgotPasswordSend}
                </button>
              </form>
            )}
            <button
              onClick={() => { setShowForgot(false); setForgotSent(false); setForgotEmail('') }}
              className="w-full text-xs text-texto-tenue hover:text-texto transition-colors"
            >
              ← {t.backToLogin}
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-dvh bg-fondo flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="flex justify-end gap-1 mb-4">
          {['es', 'en'].map((l) => (
            <button key={l} onClick={() => setLang(l)}
              className={`px-1.5 py-0.5 rounded text-xs font-bold uppercase transition-colors ${lang === l ? 'bg-accion text-sobre-color' : 'text-texto-tenue hover:text-texto'}`}>
              {l}
            </button>
          ))}
        </div>

        <div className="flex justify-center mb-8">
          <HalEye size={150} reposo />
        </div>

        <h1 className="text-center text-2xl font-bold text-texto mb-1">{nombre}</h1>
        <p className="text-center text-xs text-texto-tenue mb-8">{t.loginTagline}</p>

        {avisoSesion && (
          <AlertaError className="text-sm bg-peligro-fondo border border-peligro-borde rounded-lg px-3 py-2 mb-4">
            {t[avisoSesion] ?? t.loginError}
          </AlertaError>
        )}

        <form onSubmit={handleSubmit} className="bg-superficie rounded-xl p-6 border border-borde space-y-4">
          <div>
            <label className="block text-xs text-texto-suave mb-1 font-semibold uppercase tracking-wider">
              {t.emailLabel}
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco"
              placeholder={t.emailPlaceholder}
              required
              autoFocus
            />
          </div>

          <div>
            <label className="block text-xs text-texto-suave mb-1 font-semibold uppercase tracking-wider">
              {t.passwordLabel}
            </label>
            <PasswordInput
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco"
              placeholder="••••••••"
              required
            />
          </div>

          {error && (
            <div className="text-sm text-peligro bg-peligro-fondo border border-peligro-borde rounded-lg px-3 py-2">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 rounded-lg bg-accion hover:bg-accion-hover disabled:opacity-50 text-sobre-color font-semibold transition-colors"
          >
            {loading ? t.loggingIn : t.loginButton(nombre)}
          </button>

          <div className="text-center">
            <button
              type="button"
              onClick={() => setShowForgot(true)}
              className="text-xs text-texto-tenue hover:text-info transition-colors"
            >
              {t.forgotPassword}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
