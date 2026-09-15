import { useState } from 'react'
import { useNavigate, useSearchParams, Link } from 'react-router-dom'
import { useI18n } from '../i18n/index.jsx'
import api from '../api/client'
import PasswordInput from '../components/PasswordInput'
import { problemaDePassword } from '../lib/reglasPassword'

export default function ResetPassword() {
  const { lang, setLang, t } = useI18n()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token') || ''

  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  if (!token) {
    return (
      <div className="min-h-dvh bg-fondo flex items-center justify-center p-4">
        <div className="w-full max-w-md text-center">
          <p className="text-peligro text-sm mb-4">{t.resetPasswordInvalid}</p>
          <Link to="/login" className="text-xs text-info hover:underline">← {t.backToLogin}</Link>
        </div>
      </div>
    )
  }

  // El backend responde un código estable (2026-09-12); el texto lo pone i18n.
  // Antes se mostraba su `detail` tal cual: en español aunque la UI esté en inglés.
  const MENSAJES_RESET = {
    reset_token_invalido: t.resetPasswordInvalid,
    reset_token_usado: t.resetPasswordUsed,
    reset_token_expirado: t.resetPasswordExpired,
    reset_password_corta: t.resetPasswordShort,
    reset_password_larga: t.resetPasswordLong,
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    // La misma regla que el backend (lib/reglasPassword.js).
    const problema = problemaDePassword(password)
    if (problema === 'corta') { setError(t.resetPasswordShort); return }
    if (problema === 'larga') { setError(t.resetPasswordLong); return }
    if (password !== confirm) { setError(t.resetPasswordMismatch); return }

    setSubmitting(true)
    try {
      await api.post('/auth/reset-password', { token, password })
      setSuccess(true)
      setTimeout(() => navigate('/login'), 3000)
    } catch (err) {
      setError(MENSAJES_RESET[err.response?.data?.detail] || t.resetPasswordInvalid)
    } finally {
      setSubmitting(false)
    }
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

        <h1 className="text-center text-2xl font-bold text-texto mb-1">{t.resetPasswordTitle}</h1>
        <p className="text-center text-xs text-texto-tenue mb-8">{t.resetPasswordDesc}</p>

        <div className="bg-superficie rounded-xl p-6 border border-borde space-y-4">
          {success ? (
            <div className="text-sm text-exito bg-exito-fondo border border-exito-borde rounded-lg px-3 py-3 text-center">
              {t.resetPasswordSuccess}
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-xs text-texto-suave mb-1 font-semibold uppercase tracking-wider">
                  {t.resetPasswordLabel}
                </label>
                <PasswordInput
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco"
                  required
                  autoFocus
                />
              </div>

              <div>
                <label className="block text-xs text-texto-suave mb-1 font-semibold uppercase tracking-wider">
                  {t.resetPasswordConfirm}
                </label>
                <PasswordInput
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  className="w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco"
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
                disabled={submitting}
                className="w-full py-2.5 rounded-lg bg-accion hover:bg-accion-hover disabled:opacity-50 text-sobre-color font-semibold transition-colors"
              >
                {submitting ? t.resetPasswordSubmitting : t.resetPasswordSubmit}
              </button>
            </form>
          )}

          <div className="text-center">
            <Link to="/login" className="text-xs text-texto-tenue hover:text-texto transition-colors">
              ← {t.backToLogin}
            </Link>
          </div>
        </div>
      </div>
    </div>
  )
}
