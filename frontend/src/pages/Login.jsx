import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useJaxStore } from '../store/useJaxStore'
import { useI18n } from '../i18n/index.jsx'
import api from '../api/client'

export default function Login() {
  const login = useJaxStore((s) => s.login)
  const { lang, setLang, t } = useI18n()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPwd, setShowPwd] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  // Forgot password state
  const [showForgot, setShowForgot] = useState(false)
  const [forgotEmail, setForgotEmail] = useState('')
  const [forgotSending, setForgotSending] = useState(false)
  const [forgotSent, setForgotSent] = useState(false)

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
      } else {
        setError(t.loginError)
      }
    } finally {
      setLoading(false)
    }
  }

  async function handleForgot(e) {
    e.preventDefault()
    setForgotSending(true)
    try {
      await api.post('/auth/forgot-password', { email: forgotEmail })
      setForgotSent(true)
    } catch {
      setForgotSent(true)
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
            <div className="relative">
              <input
                type={showPwd ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 pr-10 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
                placeholder="••••••••"
                required
              />
              <button
                type="button"
                onClick={() => setShowPwd(v => !v)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors"
                title={showPwd ? t.hidePassword : t.showPassword}
              >
                {showPwd ? (
                  <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" viewBox="0 0 20 20" fill="currentColor">
                    <path d="M10 3C5 3 1.73 7.11 1 10c.73 2.89 4 7 9 7s8.27-4.11 9-7c-.73-2.89-4-7-9-7zm0 12a5 5 0 110-10 5 5 0 010 10zm0-8a3 3 0 100 6 3 3 0 000-6z" />
                  </svg>
                ) : (
                  <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" viewBox="0 0 20 20" fill="currentColor">
                    <path fillRule="evenodd" d="M3.28 2.22a.75.75 0 00-1.06 1.06l14.5 14.5a.75.75 0 101.06-1.06l-1.745-1.745a11.806 11.806 0 002.908-3.97C17.547 8.383 14.476 5 10 5a10.966 10.966 0 00-4.31.858L3.28 2.22zM5.94 7.16l1.39 1.39a3 3 0 004.12 4.12l1.39 1.39A5 5 0 015.94 7.16zM10 15c-1.42 0-2.737-.37-3.874-1.02l1.568-1.567A3 3 0 0010 13a3 3 0 003-3 3 3 0 00-.413-1.507l1.568-1.567A8.03 8.03 0 0118 10c-.973 2.6-4.027 5-8 5z" clipRule="evenodd" />
                  </svg>
                )}
              </button>
            </div>
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
