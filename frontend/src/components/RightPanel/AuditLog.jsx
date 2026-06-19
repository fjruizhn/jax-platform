import { useEffect, useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'

const EVENT_COLOR = {
  ENVELOPE_ACCEPTED:   'text-green-400',
  ENVELOPE_REJECTED:   'text-red-400',
  EXECUTION_SUCCESS:   'text-emerald-400',
  EXECUTION_FAILED:    'text-red-400',
  KILL_SWITCH_ACTIVE:  'text-red-500',
  KILL_SWITCH_CLEARED: 'text-blue-400',
}

function eventColor(event) {
  if (EVENT_COLOR[event]) return EVENT_COLOR[event]
  if (event?.includes('REJECT') || event?.includes('FAIL')) return 'text-red-400'
  if (event?.includes('SUCCESS') || event?.includes('ACCEPT')) return 'text-green-400'
  return 'text-slate-400'
}

export default function AuditLog() {
  const { t } = useI18n()
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)

  async function fetchAudit() {
    try {
      const { data } = await api.get('/audit')
      setEvents(data.events || [])
    } catch {
      // LAS MANOS puede estar caído
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchAudit()
    const id = setInterval(fetchAudit, 10_000)
    return () => clearInterval(id)
  }, [])

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-2 border-b border-slate-700 flex items-center justify-between">
        <span className="text-xs font-bold uppercase tracking-widest text-slate-500">
          {t.auditLog}
        </span>
        <span className="text-xs text-slate-700">LAS MANOS</span>
      </div>
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="px-3 py-4 text-xs text-slate-700">{t.loading}</div>
        ) : events.length === 0 ? (
          <div className="px-3 py-4 text-xs text-slate-700">{t.noEventsYet}</div>
        ) : (
          events.map((ev, i) => (
            <div key={i} className="px-3 py-1.5 border-b border-slate-800 last:border-0">
              <div className="flex items-center gap-2">
                <span className={`text-xs font-mono font-semibold flex-shrink-0 ${eventColor(ev.event)}`}>
                  {ev.event || '—'}
                </span>
                {ev.layer && (
                  <span className="text-xs text-slate-600 flex-shrink-0">[{ev.layer}]</span>
                )}
              </div>
              {ev.reason && (
                <div className="text-xs text-slate-500 mt-0.5 truncate">{ev.reason}</div>
              )}
              {ev['@timestamp'] && (
                <div className="text-xs text-slate-700 mt-0.5">
                  {new Date(ev['@timestamp']).toLocaleTimeString('es-HN')}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
