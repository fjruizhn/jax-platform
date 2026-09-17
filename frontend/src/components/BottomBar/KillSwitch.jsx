import { memo, useEffect, useRef, useState } from 'react'
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
//
// Fix round 1 (2026-09-17): el aviso de error guarda el estado del freno al
// que pertenece (leído del store DESPUÉS del fallo, con R5 ya aplicado) y sólo
// se pinta mientras ese estado siga vigente: si el freno cambia por otro
// camino (WS, loadState, 423), el aviso ya no describe nada. Y tras una acción
// PROPIA que cambia de rama, el disparador del diálogo ya no existe y Dialogo
// no puede devolverle el foco: se lleva al control de la rama nueva (el aviso
// del freno o KILL). Un cambio externo no mueve el foco.
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
  const avisoDelFreno = useRef(null)
  const botonKill = useRef(null)
  // Estado del freno en el que tiene que quedar el foco tras la acción propia.
  const focoPendiente = useRef(null)

  useEffect(() => {
    if (focoPendiente.current === null || dialogo) return
    if (focoPendiente.current === activo) (activo ? avisoDelFreno : botonKill).current?.focus()
    focoPendiente.current = null
  })

  async function ejecutar(accion, claveGenerica) {
    setError(null)
    setEnviando(true)
    try {
      await accion()
      focoPendiente.current = useJaxStore.getState().killSwitchActive
    } catch (err) {
      setError({ clave: ERRORES[codigoDe(err)] ?? claveGenerica, activo: useJaxStore.getState().killSwitchActive })
    } finally {
      setEnviando(false)
      setDialogo(null)
    }
  }

  function abrir(cual) {
    setError(null)
    setDialogo(cual)
  }

  const aviso = error && error.activo === activo && <span role="alert" className="text-xs text-peligro">{t[error.clave]}</span>

  if (activo) {
    return (
      <div className="flex items-center gap-2">
        <div ref={avisoDelFreno} tabIndex={-1} className="flex items-center gap-2 px-3 py-1 rounded-lg bg-peligro-fondo border border-peligro-solido text-peligro text-xs font-bold">
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
        ref={botonKill}
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
