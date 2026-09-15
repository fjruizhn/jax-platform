import { memo } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import { colorToken } from '../../tema/tokens'

const STATUS_DOT = {
  idle: 'bg-texto-tenue',
  thinking: 'bg-exito animate-pulse',
  error: 'bg-peligro',
  offline: 'bg-borde',
}

// Superficie opaca siempre (spec 2026-09-14-tema-tokens §3.3, Ruling 30):
// nada de tinte translúcido del color de la faceta bajo el nombre. La faceta
// activa se marca con borde, halo y nombre de su color; la inactiva, en
// hover, cambia el borde -- no el fondo: superficie-2 bajo texto-tenue es un
// par prohibido.
function FacetCard({ facet, active }) {
  const { t } = useI18n()

  const STATUS_LABELS = {
    idle: t.statusIdle,
    thinking: t.statusThinking,
    error: t.statusError,
    offline: t.statusOffline,
  }

  return (
    <div
      className={`px-3 py-2 rounded-lg border transition-all cursor-default bg-superficie ${
        active ? '' : 'border-borde hover:border-borde-control'
      }`}
      style={active ? {
        borderColor: colorToken(facet.token),
        boxShadow: `0 0 8px ${colorToken(facet.token, 0.25)}`,
      } : {}}
    >
      <div className="flex items-center gap-2">
        <span
          className={`w-2 h-2 rounded-full flex-shrink-0 ${STATUS_DOT[facet.status] || 'bg-texto-tenue'}`}
          style={facet.status === 'thinking' ? { backgroundColor: colorToken(facet.token) } : {}}
        />
        <span
          className={`text-sm font-semibold capitalize truncate ${active ? '' : 'text-texto'}`}
          style={active ? { color: colorToken(facet.token) } : undefined}
        >
          {facet.name}
        </span>
      </div>
      <div className="text-xs text-texto-tenue mt-0.5 ml-4">
        {STATUS_LABELS[facet.status] || facet.status}
      </div>
      {facet.last_message && (
        <div className="text-xs text-texto-suave mt-1 ml-4 truncate">
          {facet.last_message}
        </div>
      )}
    </div>
  )
}

export default memo(FacetCard)
