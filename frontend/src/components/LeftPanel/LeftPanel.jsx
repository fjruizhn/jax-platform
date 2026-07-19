import { memo } from 'react'
import { useJaxStore } from '../../store/useJaxStore'
import { useI18n } from '../../i18n/index.jsx'
import FacetCard from './FacetCard'

const FACET_ORDER = ['jax_local', 'jekyll', 'hyde', 'hipatia', 'thot', 'kimi', 'ada']

function LeftPanel() {
  const facets = useJaxStore((s) => s.facets)
  const lasManos = useJaxStore((s) => s.lasManos)
  const wsStatus = useJaxStore((s) => s.wsStatus)
  const { t } = useI18n()

  return (
    <div className="flex flex-col h-full bg-slate-900 border-r border-slate-700">
      <div className="px-4 py-3 border-b border-slate-700">
        <h2 className="text-xs font-bold uppercase tracking-widest text-slate-400">
          {t.facets}
        </h2>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {FACET_ORDER.map((name) => {
          const facet = facets[name]
          if (!facet) return null
          return (
            <FacetCard
              key={name}
              facet={facet}
              active={facet.status === 'thinking'}
            />
          )
        })}
      </div>

      <div className="px-4 py-3 border-t border-slate-700 space-y-1">
        <div className="flex items-center gap-2 text-xs">
          <span className={`w-2 h-2 rounded-full ${lasManos ? 'bg-green-400' : 'bg-red-500'}`} />
          <span className="text-slate-400">{t.lasManos}</span>
          <span className={lasManos ? 'text-green-400' : 'text-red-400'}>
            {lasManos ? t.alive : t.down}
          </span>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className={`w-2 h-2 rounded-full ${
            wsStatus === 'connected' ? 'bg-blue-400' : 'bg-yellow-500'
          }`} />
          <span className="text-slate-400">WS</span>
          <span className="text-slate-500">{wsStatus}</span>
        </div>
      </div>
    </div>
  )
}

export default memo(LeftPanel)
