import { useI18n } from '../../i18n/index.jsx'

const STATUS_DOT = {
  idle: 'bg-gray-500',
  thinking: 'bg-green-400 animate-pulse',
  error: 'bg-red-500',
  offline: 'bg-gray-700',
}

export default function FacetCard({ facet, active }) {
  const { t } = useI18n()

  const STATUS_LABELS = {
    idle: t.statusIdle,
    thinking: t.statusThinking,
    error: t.statusError,
    offline: t.statusOffline,
  }

  return (
    <div
      className={`
        px-3 py-2 rounded-lg border transition-all cursor-default
        ${active
          ? 'border-opacity-80 bg-opacity-10'
          : 'border-slate-700 bg-slate-800 hover:bg-slate-750'}
      `}
      style={active ? {
        borderColor: facet.color,
        backgroundColor: facet.color + '15',
        boxShadow: `0 0 8px ${facet.color}40`,
      } : {}}
    >
      <div className="flex items-center gap-2">
        <span
          className={`w-2 h-2 rounded-full flex-shrink-0 ${STATUS_DOT[facet.status] || 'bg-gray-500'}`}
          style={facet.status === 'thinking' ? { backgroundColor: facet.color } : {}}
        />
        <span
          className="text-sm font-semibold capitalize truncate"
          style={{ color: active ? facet.color : '#e2e8f0' }}
        >
          {facet.name}
        </span>
      </div>
      <div className="text-xs text-slate-500 mt-0.5 ml-4">
        {STATUS_LABELS[facet.status] || facet.status}
      </div>
      {facet.last_message && (
        <div className="text-xs text-slate-400 mt-1 ml-4 truncate">
          {facet.last_message}
        </div>
      )}
    </div>
  )
}
