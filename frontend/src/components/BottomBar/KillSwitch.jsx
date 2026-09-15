import { memo, useState } from 'react'
import { useJaxStore } from '../../store/useJaxStore'
import { useI18n } from '../../i18n/index.jsx'

function KillSwitch() {
  const killSwitchActive = useJaxStore((s) => s.killSwitchActive)
  const activateKillSwitch = useJaxStore((s) => s.activateKillSwitch)
  const { t } = useI18n()
  const [confirming, setConfirming] = useState(false)

  if (killSwitchActive) {
    return (
      <div className="flex items-center gap-2 px-3 py-1 rounded-lg bg-peligro-fondo border border-peligro-solido text-peligro text-xs font-bold">
        <span className="w-2 h-2 rounded-full bg-peligro-solido" />
        {t.killSwitchActive}
      </div>
    )
  }

  if (confirming) {
    return (
      <div className="flex items-center gap-2">
        <span className="text-xs text-peligro">{t.killConfirm}</span>
        <button
          onClick={() => { activateKillSwitch(); setConfirming(false) }}
          className="px-2 py-1 rounded bg-peligro-solido hover:bg-peligro-solido-hover text-sobre-color text-xs font-bold"
        >
          {t.killConfirmYes}
        </button>
        <button
          onClick={() => setConfirming(false)}
          className="px-2 py-1 rounded bg-superficie-2 text-texto hover:text-texto-fuerte text-xs"
        >
          {t.cancel}
        </button>
      </div>
    )
  }

  return (
    <button
      onClick={() => setConfirming(true)}
      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-peligro-fondo border border-peligro-borde hover:border-peligro-solido text-peligro text-xs font-bold uppercase tracking-widest transition-all"
      title={t.killTitle}
    >
      <span className="w-2 h-2 rounded-full bg-peligro-solido animate-pulse" />
      {t.killButton}
    </button>
  )
}

export default memo(KillSwitch)
