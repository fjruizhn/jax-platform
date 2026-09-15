import { useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import PasswordInput from '../PasswordInput'
import Dialogo from '../Dialogo'
import { problemaDePassword } from '../../lib/reglasPassword'

// Fijar la contraseña de otro usuario (2026-09-15, decisiones de Fernando que
// revierten U2). La regla es la única (lib/reglasPassword.js = backend). El
// POST, el toast y el cierre son del padre (onFijar). Colores por tokens
// (src/tema/tokens.css), misma estructura que MiCuentaModal.
const CAMPO = 'w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto placeholder-texto-tenue focus:outline-none focus:border-foco'
const ETIQUETA = 'block text-xs text-texto-suave mb-1 font-semibold uppercase tracking-wider'

export default function FijarPasswordModal({ usuario, onFijar, onCerrar }) {
  const { t } = useI18n()
  const [nueva, setNueva] = useState('')
  const [confirmar, setConfirmar] = useState('')
  const [error, setError] = useState('')
  const [guardando, setGuardando] = useState(false)

  async function enviar(e) {
    e.preventDefault()
    setError('')
    const problema = problemaDePassword(nueva)
    if (problema === 'corta') { setError(t.resetPasswordShort); return }
    if (problema === 'larga') { setError(t.resetPasswordLong); return }
    if (nueva !== confirmar) { setError(t.resetPasswordMismatch); return }
    setGuardando(true)
    try {
      await onFijar(nueva)
    } finally {
      setGuardando(false)
    }
  }

  return (
    <Dialogo idTitulo="fijar-password-titulo" titulo={t.adminSetPasswordTitle(usuario.email)} claseTitulo="text-sm font-semibold text-texto mb-1" onCerrar={onCerrar}>
      <p className="text-xs text-texto-tenue mb-4">{t.adminSetPasswordHint}</p>
      <form onSubmit={enviar} className="space-y-3">
        <div>
          <label htmlFor="fijar-nueva" className={ETIQUETA}>{t.resetPasswordLabel}</label>
          <PasswordInput id="fijar-nueva" value={nueva} onChange={(e) => setNueva(e.target.value)} autoComplete="new-password" required className={CAMPO} />
        </div>
        <div>
          <label htmlFor="fijar-confirmar" className={ETIQUETA}>{t.resetPasswordConfirm}</label>
          <PasswordInput id="fijar-confirmar" value={confirmar} onChange={(e) => setConfirmar(e.target.value)} autoComplete="new-password" required className={CAMPO} />
        </div>
        {/* Región montada siempre: un error insertado junto con su región no
            se anuncia de forma confiable. Las clases de caja van sólo con
            texto. */}
        <div role="status" className={error ? 'text-sm text-peligro bg-peligro-fondo border border-peligro-borde rounded-lg px-3 py-2' : undefined}>{error || null}</div>
        <div className="flex gap-2 justify-end pt-2">
          <button type="button" onClick={onCerrar} className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors">{t.adminCreateCancel}</button>
          <button type="submit" disabled={guardando} className="px-4 py-1.5 rounded-lg bg-accion hover:bg-accion-hover text-sobre-color text-sm font-semibold disabled:opacity-50 transition-colors">
            {guardando ? t.adminSetPasswordSubmitting : t.adminUserSetPassword}
          </button>
        </div>
      </form>
    </Dialogo>
  )
}
