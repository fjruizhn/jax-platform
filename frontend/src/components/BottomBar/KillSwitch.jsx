import { memo, useState } from 'react'
import { useJaxStore } from '../../store/useJaxStore'
import { useI18n } from '../../i18n/index.jsx'
import { codigoDe } from '../../api/errores'
import Dialogo from '../Dialogo'
import ConfirmacionSuma from '../ConfirmacionSuma'

// Kill switch real (2026-09-16, frente B). Muestra el estado REAL (loadState
// + eventos de WS + el 423 que enciende el interceptor). Sólo un superadmin
// activa y reanuda (el backend lo exige); cualquiera ve el aviso. Activar es
// rápido (Dialogo); reanudar pide la suma (ConfirmacionSuma). Si la llamada
// falla, se dice: no hay catch vacío.
const ERRORES = {
  kill_switch_no_escribible: 'killSwitchErrorNoEscribible',
  kill_switch_auditoria_fallida: 'killSwitchErrorAuditoria',
}

function KillSwitch() {
  const activo = useJaxStore((s) => s.killSwitchActive)
  const esSuperadmin = useJaxStore((s) => s.user?.role === 'superadmin')
  const activar = useJaxStore((s) => s.activarKillSwitch)
  const reanudar = useJaxStore((s) => s.reanudarKillSwitch)
  const { t } = useI18n()
  const [dialogo, setDialogo] = useState(null)
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState(null)

  async function ejecutar(accion, claveGenerica) {
    setError(null)
    setEnviando(true)
    try {
      await accion()
    } catch (err) {
      setError(ERRORES[codigoDe(err)] ?? claveGenerica)
    } finally {
      setEnviando(false)
      setDialogo(null)
    }
  }

  function abrir(cual) {
    setError(null)
    setDialogo(cual)
  }

  const aviso = error && <span role="alert" className="text-xs text-peligro">{t[error]}</span>

  if (activo) {
    return (
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-2 px-3 py-1 rounded-lg bg-peligro-fondo border border-peligro-solido text-peligro text-xs font-bold">
          <span className="w-2 h-2 rounded-full bg-peligro-solido" />
          {t.killSwitchActive}
        </div>
        {esSuperadmin && (
          <button
            type="button"
            onClick={() => abrir('reanudar')}
            className="px-2 py-1 rounded bg-superficie-2 text-texto hover:text-texto-fuerte text-xs font-semibold"
          >
            {t.killResumeButton}
          </button>
        )}
        {aviso}
        {dialogo === 'reanudar' && (
          <ConfirmacionSuma
            titulo={t.killResumeTitle}
            mensaje={t.killResumeMessage}
            textoConfirmar={t.killResumeConfirm}
            onConfirmar={() => ejecutar(reanudar, 'killSwitchErrorReanudar')}
            onCancelar={() => setDialogo(null)}
          />
        )}
      </div>
    )
  }

  if (!esSuperadmin) return null

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => abrir('activar')}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-peligro-fondo border border-peligro-borde hover:border-peligro-solido text-peligro text-xs font-bold uppercase tracking-widest transition-all"
        title={t.killTitle}
      >
        <span className="w-2 h-2 rounded-full bg-peligro-solido animate-pulse" />
        {t.killButton}
      </button>
      {aviso}
      {dialogo === 'activar' && (
        <Dialogo idTitulo="kill-switch-activar-titulo" titulo={t.killConfirmTitle} onCerrar={() => setDialogo(null)}>
          <p className="text-sm text-texto-suave mb-4">{t.killConfirmMessage}</p>
          <div className="flex gap-2 justify-end">
            <button
              type="button"
              onClick={() => setDialogo(null)}
              className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors"
            >
              {t.cancel}
            </button>
            <button
              type="button"
              disabled={enviando}
              onClick={() => ejecutar(activar, 'killSwitchErrorActivar')}
              className="px-4 py-1.5 rounded-lg bg-peligro-solido hover:bg-peligro-solido-hover text-sobre-color text-sm font-semibold disabled:opacity-50 transition-colors"
            >
              {t.killConfirmYes}
            </button>
          </div>
        </Dialogo>
      )}
    </div>
  )
}

export default memo(KillSwitch)
