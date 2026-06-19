import { useState } from 'react'
import { useJaxStore } from '../store/useJaxStore'
import { useI18n } from '../i18n/index.jsx'

export default function Login() {
  const { login } = useJaxStore()
  const { lang, setLang, t } = useI18n()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(email, password)
    } catch (err) {
      setError(err.response?.data?.detail || t.loginError)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-hal-bg flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Language selector */}
        <div className="flex justify-end gap-1 mb-4">
          {['es', 'en'].map((l) => (
            <button
              key={l}
              onClick={() => setLang(l)}
              className={`px-1.5 py-0.5 rounded text-xs font-bold uppercase transition-colors ${
                lang === l
                  ? 'bg-blue-600 text-white'
                  : 'text-slate-500 hover:text-slate-300'
              }`}
            >
              {l}
            </button>
          ))}
        </div>

        {/* Ojo HAL pequeño */}
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
            <input
              type="password"
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
        </form>
      </div>
    </div>
  )
}
