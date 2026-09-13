import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useJaxStore } from '../store/useJaxStore'
import { useI18n } from '../i18n/index.jsx'
import api from '../api/client'
import PasswordInput from '../components/PasswordInput'

export default function Login() {
  const login = useJaxStore((s) => s.login)
  const { lang, setLang, t } = useI18n()
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
    setLoading(true)
    try {
      await login(email, password)
      navigate('/')
    } catch (err) {
      const status = err.response?.status
      const detail = err.response?.data?.detail || ''

      if (status === 423) {
        setError(t.accountLocked)
        const match = detail.match(/(\d+)\s*minuto/)
        if (match) setError(t.accountLockedMinutes(match[1]))
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
      <div className="min-h-dvh bg-hal-bg flex items-center justify-center p-4">
        <div className="w-full max-w-md">
          <div className="flex justify-end gap-1 mb-4">
            {['es', 'en'].map((l) => (
              <button key={l} onClick={() => setLang(l)}
                className={`px-1.5 py-0.5 rounded text-xs font-bold uppercase transition-colors ${lang === l ? 'bg-blue-600 text-white' : 'text-slate-500 hover:text-slate-300'}`}>
                {l}
              </button>
            ))}
          </div>

          <h1 className="text-center text-2xl font-bold text-slate-200 mb-1">{t.forgotPasswordTitle}</h1>
          <p className="text-center text-xs text-slate-600 mb-8">{t.forgotPasswordDesc}</p>

          <div className="bg-slate-800 rounded-xl p-6 border border-slate-700 space-y-4">
            {forgotSent ? (
              <div className="text-sm text-green-400 bg-green-900/30 border border-green-800 rounded-lg px-3 py-3 text-center">
                {t.forgotPasswordSent}
              </div>
            ) : (
              <form onSubmit={handleForgot} className="space-y-4">
                <div>
                  <label className="block text-xs text-slate-400 mb-1 font-semibold uppercase tracking-wider">
                    {t.emailLabel}
                  </label>
                  <input
                    type="email"
                    value={forgotEmail}
                    onChange={(e) => setForgotEmail(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
                    required
                    autoFocus
                  />
                </div>
                {forgotError && (
                  <div className="text-sm text-red-400 bg-red-900/30 border border-red-800 rounded-lg px-3 py-2">
                    {forgotError}
                  </div>
                )}
                <button
                  type="submit"
                  disabled={forgotSending}
                  className="w-full py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-semibold transition-colors"
                >
                  {forgotSending ? t.forgotPasswordSending : t.forgotPasswordSend}
                </button>
              </form>
            )}
            <button
              onClick={() => { setShowForgot(false); setForgotSent(false); setForgotEmail('') }}
              className="w-full text-xs text-slate-500 hover:text-slate-300 transition-colors"
            >
              ← {t.backToLogin}
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-dvh bg-hal-bg flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="flex justify-end gap-1 mb-4">
          {['es', 'en'].map((l) => (
            <button key={l} onClick={() => setLang(l)}
              className={`px-1.5 py-0.5 rounded text-xs font-bold uppercase transition-colors ${lang === l ? 'bg-blue-600 text-white' : 'text-slate-500 hover:text-slate-300'}`}>
              {l}
            </button>
          ))}
        </div>

        <div className="flex justify-center mb-8">
          <svg width="80" height="80" viewBox="0 0 80 80">
            <circle cx="40" cy="40" r="36" fill="#0f172a" stroke="#1e293b" strokeWidth="2" />
            <circle cx="40" cy="40" r="30" fill="none" stroke="#3b82f6" strokeWidth="1" opacity="0.3" />
            <circle cx="40" cy="40" r="16" fill="#3b82f6" opacity="0.9" />
            <circle cx="40" cy="40" r="12" fill="#0f172a" opacity="0.5" />
            <circle cx="40" cy="40" r="6" fill="#0a0f1a" />
            <circle cx="34" cy="34" r="2.5" fill="white" opacity="0.6" />
          </svg>
        </div>

        <h1 className="text-center text-2xl font-bold text-slate-200 mb-1">{t.loginTitle}</h1>
        <p className="text-center text-xs text-slate-600 mb-8">{t.loginTagline}</p>

        <form onSubmit={handleSubmit} className="bg-slate-800 rounded-xl p-6 border border-slate-700 space-y-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1 font-semibold uppercase tracking-wider">
              {t.emailLabel}
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
              placeholder="fernando@rich-hn.com"
              required
              autoFocus
            />
          </div>

          <div>
            <label className="block text-xs text-slate-400 mb-1 font-semibold uppercase tracking-wider">
              {t.passwordLabel}
            </label>
            <PasswordInput
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
              placeholder="••••••••"
              required
            />
          </div>

          {error && (
            <div className="text-sm text-red-400 bg-red-900/30 border border-red-800 rounded-lg px-3 py-2">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-semibold transition-colors"
          >
            {loading ? t.loggingIn : t.loginButton}
          </button>

          <div className="text-center">
            <button
              type="button"
              onClick={() => setShowForgot(true)}
              className="text-xs text-slate-500 hover:text-blue-400 transition-colors"
            >
              {t.forgotPassword}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
