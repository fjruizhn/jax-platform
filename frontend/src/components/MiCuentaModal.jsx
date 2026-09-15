import { useState } from 'react'
import { useI18n } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
import PasswordInput from './PasswordInput'
import { problemaDePassword } from '../lib/reglasPassword'
import { useCerrarConEscape } from '../lib/useCerrarConEscape'

// Mi cuenta (2026-09-12, admin usuarios etapa 4, spec §3.4): cambiar la propia
// contraseña. Exige la actual; al guardar se cierran las otras sesiones.
// Colores por tokens (src/tema/tokens.css), misma estructura que el modal de
// alta de AdminUsers.jsx.
const CAMPO = 'w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto placeholder-texto-tenue focus:outline-none focus:border-foco'
const ETIQUETA = 'block text-xs text-texto-suave mb-1 font-semibold uppercase tracking-wider'

export default function MiCuentaModal({ onCerrar }) {
  const { t } = useI18n()
  const cambiarMiPassword = useJaxStore((s) => s.cambiarMiPassword)
  useCerrarConEscape(onCerrar)
  const [actual, setActual] = useState('')
  const [nueva, setNueva] = useState('')
  const [confirmar, setConfirmar] = useState('')
  const [error, setError] = useState('')
  const [hecho, setHecho] = useState(false)
  const [enviando, setEnviando] = useState(false)

  const MENSAJES = {
    password_actual_incorrecta: t.myAccountWrongCurrent,
    password_corta: t.resetPasswordShort,
    password_larga: t.resetPasswordLong,
  }

  async function enviar(e) {
    e.preventDefault()
    setError('')
    const problema = problemaDePassword(nueva)
    if (problema === 'corta') { setError(t.resetPasswordShort); return }
    if (problema === 'larga') { setError(t.resetPasswordLong); return }
    if (nueva !== confirmar) { setError(t.resetPasswordMismatch); return }
    setEnviando(true)
    try {
      await cambiarMiPassword(actual, nueva)
      setHecho(true)
    } catch (err) {
      if (err?.response?.status === 429) setError(t.myAccountTooMany)
      else setError(MENSAJES[err?.response?.data?.detail] || t.myAccountError)
    } finally {
      setEnviando(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-fondo/70 flex items-center justify-center z-50">
      <div role="dialog" aria-modal="true" aria-labelledby="mi-cuenta-titulo"
        className="bg-superficie border border-borde rounded-xl p-6 w-full max-w-md shadow-2xl">
        <h2 id="mi-cuenta-titulo" className="text-sm font-semibold text-texto mb-1">{t.myAccount}</h2>
        <p className="text-xs text-texto-tenue mb-4">{t.myAccountChangePassword}</p>
        {hecho ? (
          <div role="status" className="text-sm text-exito bg-exito-fondo border border-exito-borde rounded-lg px-3 py-3">
            {t.myAccountDone}
          </div>
        ) : (
          <form onSubmit={enviar} className="space-y-3">
            <div>
              <label htmlFor="mi-cuenta-actual" className={ETIQUETA}>{t.myAccountCurrent}</label>
              <PasswordInput id="mi-cuenta-actual" value={actual} onChange={(e) => setActual(e.target.value)} className={CAMPO} autoComplete="current-password" required />
            </div>
            <div>
              <label htmlFor="mi-cuenta-nueva" className={ETIQUETA}>{t.resetPasswordLabel}</label>
              <PasswordInput id="mi-cuenta-nueva" value={nueva} onChange={(e) => setNueva(e.target.value)} className={CAMPO} autoComplete="new-password" required />
            </div>
            <div>
              <label htmlFor="mi-cuenta-confirmar" className={ETIQUETA}>{t.resetPasswordConfirm}</label>
              <PasswordInput id="mi-cuenta-confirmar" value={confirmar} onChange={(e) => setConfirmar(e.target.value)} className={CAMPO} autoComplete="new-password" required />
            </div>
            {error && (
              <div className="text-sm text-peligro bg-peligro-fondo border border-peligro-borde rounded-lg px-3 py-2">{error}</div>
            )}
            <div className="flex gap-2 justify-end pt-2">
              <button type="button" onClick={onCerrar} className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors">{t.adminCreateCancel}</button>
              <button type="submit" disabled={enviando} className="px-4 py-1.5 rounded-lg bg-accion hover:bg-accion-hover text-sobre-color text-sm font-semibold disabled:opacity-50 transition-colors">
                {enviando ? t.myAccountSubmitting : t.myAccountSubmit}
              </button>
            </div>
          </form>
        )}
        {hecho && (
          <div className="flex justify-end pt-4">
            <button type="button" onClick={onCerrar} className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors">{t.adminHistoryClose}</button>
          </div>
        )}
      </div>
    </div>
  )
}
