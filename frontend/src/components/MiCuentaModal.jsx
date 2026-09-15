import { useState, useEffect, useRef } from 'react'
import { useI18n } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
import PasswordInput from './PasswordInput'
import { problemaDePassword } from '../lib/reglasPassword'
import Dialogo from './Dialogo'

// Mi cuenta (2026-09-12, admin usuarios etapa 4, spec §3.4): cambiar la propia
// contraseña. Exige la actual; al guardar se cierran las otras sesiones.
// Colores por tokens (src/tema/tokens.css), misma estructura que el modal de
// alta (CrearUsuarioModal). El comportamiento de diálogo (portal, #root inert,
// foco, Escape) lo pone Dialogo (Ruling U27, 2026-09-15).
//
// `obligatorio` (2026-09-15, U34): el admin fijó la contraseña. RequireAuth
// muestra este diálogo EN LUGAR de la app: sin Cancelar, sin Escape y sin el
// bloque "Cerrar" del éxito; sigue pidiendo la actual (P2). La única salida
// además de cambiarla es "Cerrar sesión" (/auth/logout admite la marca). Al
// terminar, el store apaga la marca, RequireAuth monta la app y un toast avisa.
const CAMPO = 'w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto placeholder-texto-tenue focus:outline-none focus:border-foco'
const ETIQUETA = 'block text-xs text-texto-suave mb-1 font-semibold uppercase tracking-wider'

export default function MiCuentaModal({ onCerrar, obligatorio = false }) {
  const { t } = useI18n()
  const cambiarMiPassword = useJaxStore((s) => s.cambiarMiPassword)
  const logout = useJaxStore((s) => s.logout)
  const addToast = useJaxStore((s) => s.addToast)
  const [actual, setActual] = useState('')
  const [nueva, setNueva] = useState('')
  const [confirmar, setConfirmar] = useState('')
  const [error, setError] = useState('')
  const [hecho, setHecho] = useState(false)
  const [enviando, setEnviando] = useState(false)
  // Tras el éxito el form se desmonta: el foco va al botón Cerrar en vez de
  // caer a body (fix round 1 del re-review final, 2026-09-15).
  const cerrarRef = useRef(null)
  useEffect(() => {
    if (hecho) cerrarRef.current?.focus()
  }, [hecho])

  const MENSAJES = {
    password_actual_incorrecta: t.myAccountWrongCurrent,
    password_corta: t.resetPasswordShort,
    password_larga: t.resetPasswordLong,
    // P1 (U34): sólo con la marca prendida.
    password_igual_a_la_actual: t.myAccountSameAsCurrent,
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
      if (obligatorio) {
        // El store ya apagó la marca: RequireAuth desmonta este diálogo.
        addToast?.({ type: 'success', message: t.forcedChangeDone })
        return
      }
      setHecho(true)
    } catch (err) {
      if (err?.response?.status === 429) setError(t.myAccountTooMany)
      else setError(MENSAJES[err?.response?.data?.detail] || t.myAccountError)
    } finally {
      setEnviando(false)
    }
  }

  return (
    <Dialogo idTitulo="mi-cuenta-titulo" titulo={obligatorio ? t.forcedChangeTitle : t.myAccount} claseTitulo="text-sm font-semibold text-texto mb-1" cerrable={!obligatorio} onCerrar={onCerrar}>
        <p className="text-xs text-texto-tenue mb-4">{obligatorio ? t.forcedChangeIntro : t.myAccountChangePassword}</p>
        {/* La región role="status" vive montada y vacía todo el diálogo y
            recibe el texto al terminar: una región viva insertada ya con su
            contenido no se anuncia de forma confiable. Las clases de caja van
            sólo con texto, para no pintar una caja vacía. */}
        <div role="status" className={hecho ? 'text-sm text-exito bg-exito-fondo border border-exito-borde rounded-lg px-3 py-3' : undefined}>
          {hecho ? t.myAccountDone : null}
        </div>
        {!hecho && (
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
              {obligatorio
                ? <button type="button" onClick={() => logout()} className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-peligro transition-colors">{t.forcedChangeLogout}</button>
                : <button type="button" onClick={onCerrar} className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors">{t.adminCreateCancel}</button>}
              <button type="submit" disabled={enviando} className="px-4 py-1.5 rounded-lg bg-accion hover:bg-accion-hover text-sobre-color text-sm font-semibold disabled:opacity-50 transition-colors">
                {enviando ? t.myAccountSubmitting : t.myAccountSubmit}
              </button>
            </div>
          </form>
        )}
        {hecho && (
          <div className="flex justify-end pt-4">
            <button ref={cerrarRef} type="button" onClick={onCerrar} className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors">{t.adminHistoryClose}</button>
          </div>
        )}
    </Dialogo>
  )
}
