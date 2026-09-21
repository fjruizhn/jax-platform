import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'

function ServiceCard({ service, t }) {
  const isOk = service.status === 'alive' || service.status === 'connected'
  const texto = isOk
    ? (service.status === 'connected' ? t.serviceConnected : t.serviceAlive)
    : service.status === 'sin_configurar' ? t.serviceNotConfigured
    : service.status === 'error' ? t.serviceError : t.serviceDown
  return (
    <div className={`rounded-lg p-4 border ${isOk ? 'border-exito-borde bg-exito-fondo' : 'border-peligro-borde bg-peligro-fondo'}`}>
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-texto">{service.name}</div>
          {service.port && <div className="text-xs text-texto">:{service.port}</div>}
        </div>
        <div className={`flex items-center gap-1.5 text-xs font-semibold ${isOk ? 'text-exito' : 'text-peligro'}`}>
          <span className={`w-2 h-2 rounded-full ${isOk ? 'bg-exito' : 'bg-peligro'}`} />
          {texto}
        </div>
      </div>
      {service.latency_ms != null && (
        <div className="text-xs text-texto mt-1">{service.latency_ms}ms</div>
      )}
    </div>
  )
}

// A-38 (2026-09-16): el tono llega como clase COMPLETA. Armarla en runtime
// (`text-${tono}`) la esconde de Tailwind y de src/tema/contraste.test.js.
function StatCard({ label, value, tono = 'text-texto', sub }) {
  return (
    <div className="rounded-lg p-4 bg-superficie border border-borde text-center">
      <div className={`text-2xl font-bold ${tono} mb-1`}>{value}</div>
      <div className="text-xs text-texto-tenue">{label}</div>
      {sub && <div className="text-xs text-texto-tenue mt-0.5">{sub}</div>}
    </div>
  )
}

export default function AdminDashboard() {
  const { t } = useI18n()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.get('/admin/dashboard')
      .then(r => setData(r.data))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const s = data?.stats

  return (
    <div>
      <h1 className="text-xl font-bold text-texto-fuerte mb-6">{t.adminDashboard}</h1>

      {loading && <div className="text-texto-tenue text-sm">{t.loading}</div>}

      {data && (
        <>
          <section className="mb-6">
            <h2 className="text-sm font-semibold text-texto-suave uppercase tracking-wider mb-3">{t.adminServicesTitle}</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {data.services.map(sv => <ServiceCard key={sv.name} service={sv} t={t} />)}
            </div>
          </section>

          <section className="mb-6">
            <h2 className="text-sm font-semibold text-texto-suave uppercase tracking-wider mb-3">{t.adminStatsTitle}</h2>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
              <StatCard
                label={t.statRequestsToday}
                value={s.messages_today}
                tono="text-info"
                sub={t.statImagesSub(s.images_generated)}
              />
              <StatCard label={t.statPipelines}    value={s.pipelines_completed} tono="text-texto-fuerte" />
              <StatCard label={t.statUsersActive}  value={s.users_active}        tono="text-exito" />
              {s.users_locked > 0 && (
                <StatCard label={t.statUsersLocked} value={s.users_locked} tono="text-aviso" />
              )}
              {/* Restricción dura (2026-09-20): siempre visible, incluso en 0
                  -- a diferencia de "Bloqueados" de arriba, acá el 0 ES la
                  buena noticia (nada pendiente de revisión de verdad). */}
              <StatCard
                label={t.statFactsUnverified}
                value={s.facts_unverified}
                tono={s.facts_unverified === 0 ? 'text-exito' : 'text-aviso'}
              />
              <StatCard
                label={t.statApiKeysLabel}
                value={`${s.api_keys_configured}/${s.api_keys_total}`}
                tono={s.api_keys_total > 0 && s.api_keys_configured === s.api_keys_total ? 'text-exito' : 'text-aviso'}
              />
              {s.ram && (
                <StatCard
                  label={t.statRam}
                  value={`${s.ram.percent}%`}
                  tono={s.ram.percent > 85 ? 'text-aviso' : 'text-texto'}
                  sub={`${s.ram.used_mb} / ${s.ram.total_mb} MB`}
                />
              )}
            </div>
          </section>
        </>
      )}
    </div>
  )
}
