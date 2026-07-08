import { useState } from 'react'
import { useNavigate, useSearchParams, Link } from 'react-router-dom'
import { useI18n } from '../i18n/index.jsx'
import api from '../api/client'

export default function ResetPassword() {
  const { lang, setLang, t } = useI18n()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token') || ''

  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [showPwd, setShowPwd] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  if (!token) {
    return (
      <div className="min-h-screen bg-hal-bg flex items-center justify-center p-4">
        <div className="w-full max-w-md text-center">
          <p className="text-red-400 text-sm mb-4">{t.resetPasswordInvalid}</p>
          <Link to="/login" className="text-xs text-blue-400 hover:text-blue-300">← {t.backToLogin}</Link>
        </div>
      </div>
    )
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    if (password.length < 8) { setError(t.resetPasswordShort); return }
    if (password !== confirm) { setError(t.resetPasswordMismatch); return }

    setSubmitting(true)
    try {
      await api.post('/auth/reset-password', { token, password })
      setSuccess(true)
      setTimeout(() => navigate('/login'), 3000)
    } catch (err) {
      const detail = err.response?.data?.detail || t.resetPasswordInvalid
      setError(detail)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen bg-hal-bg flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="flex justify-end gap-1 mb-4">
          {['es', 'en'].map((l) => (
            <button key={l} onClick={() => setLang(l)}
              className={`px-1.5 py-0.5 rounded text-xs font-bold uppercase transition-colors ${lang === l ? 'bg-blue-600 text-white' : 'text-slate-500 hover:text-slate-300'}`}>
              {l}
            </button>
          ))}
        </div>

        <h1 className="text-center text-2xl font-bold text-slate-200 mb-1">{t.resetPasswordTitle}</h1>
        <p className="text-center text-xs text-slate-600 mb-8">{t.resetPasswordDesc}</p>

        <div className="bg-slate-800 rounded-xl p-6 border border-slate-700 space-y-4">
          {success ? (
            <div className="text-sm text-green-400 bg-green-900/30 border border-green-800 rounded-lg px-3 py-3 text-center">
              {t.resetPasswordSuccess}
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-xs text-slate-400 mb-1 font-semibold uppercase tracking-wider">
                  {t.resetPasswordLabel}
                </label>
                <div className="relative">
                  <input
                    type={showPwd ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 pr-10 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
                    required
                    autoFocus
                  />
                  <button type="button" onClick={() => setShowPwd(v => !v)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300">
                    {showPwd ? '👁' : '👁‍🗨'}
                  </button>
                </div>
              </div>

              <div>
                <label className="block text-xs text-slate-400 mb-1 font-semibold uppercase tracking-wider">
                  {t.resetPasswordConfirm}
                </label>
                <input
                  type={showPwd ? 'text' : 'password'}
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
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
                disabled={submitting}
                className="w-full py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-semibold transition-colors"
              >
                {submitting ? t.resetPasswordSubmitting : t.resetPasswordSubmit}
              </button>
            </form>
          )}

          <div className="text-center">
            <Link to="/login" className="text-xs text-slate-500 hover:text-slate-300 transition-colors">
              ← {t.backToLogin}
            </Link>
          </div>
        </div>
      </div>
    </div>
  )
}
