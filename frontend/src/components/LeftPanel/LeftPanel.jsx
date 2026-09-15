import { memo } from 'react'
import { useJaxStore } from '../../store/useJaxStore'
import { useI18n } from '../../i18n/index.jsx'
import FacetCard from './FacetCard'
import HalEye from '../HalEye/HalEye'

const FACET_ORDER = ['jax_local', 'jekyll', 'hyde', 'hipatia', 'thot', 'kimi', 'ada']

function LeftPanel() {
  const facets = useJaxStore((s) => s.facets)
  const lasManos = useJaxStore((s) => s.lasManos)
  const wsStatus = useJaxStore((s) => s.wsStatus)
  const { t } = useI18n()

  return (
    <div className="flex flex-col h-full bg-fondo border-r border-borde">
      {/* Ojo HAL en la esquina (2026-09-12, pedido de Fernando): antes ocupaba
          ~270 px arriba del centro; ahí el centro queda para la conversación.
          `relative`: la etiqueta de estado del ojo se posiciona contra esto. */}
      <div className="flex-shrink-0 flex flex-col items-center justify-center py-4 relative border-b border-borde">
        <HalEye size={150} />
        <div className="mt-1 text-xs font-mono text-texto-tenue tracking-widest uppercase">
          {t.platformLabel}
        </div>
      </div>

      <div className="px-4 py-3 border-b border-borde">
        <h2 className="text-xs font-bold uppercase tracking-widest text-texto-suave">
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

      <div className="px-4 py-3 border-t border-borde space-y-1">
        <div className="flex items-center gap-2 text-xs">
          <span className={`w-2 h-2 rounded-full ${lasManos ? 'bg-exito' : 'bg-peligro-solido'}`} />
          <span className="text-texto-suave">{t.lasManos}</span>
          <span className={lasManos ? 'text-exito' : 'text-peligro'}>
            {lasManos ? t.alive : t.down}
          </span>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className={`w-2 h-2 rounded-full ${
            wsStatus === 'connected' ? 'bg-info' : 'bg-aviso'
          }`} />
          <span className="text-texto-suave">{t.wsLabel}</span>
          <span className="text-texto-tenue">{t.wsStatusLabels[wsStatus] || wsStatus}</span>
        </div>
      </div>
    </div>
  )
}

export default memo(LeftPanel)
