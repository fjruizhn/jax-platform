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
      <div className="flex items-center gap-2 px-3 py-1 rounded-lg bg-red-900 border border-red-500 text-red-300 text-xs font-bold">
        <span className="w-2 h-2 rounded-full bg-red-500" />
        {t.killSwitchActive}
      </div>
    )
  }

  if (confirming) {
    return (
      <div className="flex items-center gap-2">
        <span className="text-xs text-red-400">{t.killConfirm}</span>
        <button
          onClick={() => { activateKillSwitch(); setConfirming(false) }}
          className="px-2 py-1 rounded bg-red-600 hover:bg-red-500 text-white text-xs font-bold"
        >
          {t.killConfirmYes}
        </button>
        <button
          onClick={() => setConfirming(false)}
          className="px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 text-slate-300 text-xs"
        >
          {t.cancel}
        </button>
      </div>
    )
  }

  return (
    <button
      onClick={() => setConfirming(true)}
      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-950 border border-red-800 hover:bg-red-900 hover:border-red-600 text-red-400 hover:text-red-300 text-xs font-bold uppercase tracking-widest transition-all"
      title={t.killTitle}
    >
      <span className="w-2 h-2 rounded-full bg-red-600 animate-pulse" />
      KILL
    </button>
  )
}

export default memo(KillSwitch)
