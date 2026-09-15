import { memo, useEffect, useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'

const EVENT_COLOR = {
  ENVELOPE_ACCEPTED:   'text-exito',
  ENVELOPE_REJECTED:   'text-peligro',
  EXECUTION_SUCCESS:   'text-exito',
  EXECUTION_FAILED:    'text-peligro',
  KILL_SWITCH_ACTIVE:  'text-peligro',
  KILL_SWITCH_CLEARED: 'text-info',
}

function eventColor(event) {
  if (EVENT_COLOR[event]) return EVENT_COLOR[event]
  if (event?.includes('REJECT') || event?.includes('FAIL')) return 'text-peligro'
  if (event?.includes('SUCCESS') || event?.includes('ACCEPT')) return 'text-exito'
  return 'text-texto-suave'
}

function AuditLog() {
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
      <div className="px-3 py-2 border-b border-borde flex items-center justify-between">
        <span className="text-xs font-bold uppercase tracking-widest text-texto-tenue">
          {t.auditLog}
        </span>
        <span className="text-xs text-texto-tenue">{t.lasManos}</span>
      </div>
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="px-3 py-4 text-xs text-texto-tenue">{t.loading}</div>
        ) : events.length === 0 ? (
          <div className="px-3 py-4 text-xs text-texto-tenue">{t.noEventsYet}</div>
        ) : (
          events.map((ev, i) => (
            <div key={i} className="px-3 py-1.5 border-b border-borde last:border-0">
              <div className="flex items-center gap-2">
                <span className={`text-xs font-mono font-semibold flex-shrink-0 ${eventColor(ev.event)}`}>
                  {ev.event || '—'}
                </span>
                {ev.layer && (
                  <span className="text-xs text-texto-tenue flex-shrink-0">[{ev.layer}]</span>
                )}
              </div>
              {ev.reason && (
                <div className="text-xs text-texto-tenue mt-0.5 truncate">{ev.reason}</div>
              )}
              {ev['@timestamp'] && (
                <div className="text-xs text-texto-tenue mt-0.5">
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

export default memo(AuditLog)
