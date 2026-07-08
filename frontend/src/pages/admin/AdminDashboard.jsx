import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'

function ServiceCard({ service, t }) {
  const isOk = service.status === 'alive' || service.status === 'connected'
  return (
    <div className={`rounded-lg p-4 border ${isOk ? 'border-green-800 bg-green-950/30' : 'border-red-800 bg-red-950/30'}`}>
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-slate-200">{service.name}</div>
          {service.port && <div className="text-xs text-slate-500">:{service.port}</div>}
        </div>
        <div className={`flex items-center gap-1.5 text-xs font-semibold ${isOk ? 'text-green-400' : 'text-red-400'}`}>
          <span className={`w-2 h-2 rounded-full ${isOk ? 'bg-green-400' : 'bg-red-400'}`} />
          {isOk
            ? (service.status === 'connected' ? t.serviceConnected : t.serviceAlive)
            : (service.status === 'error' ? t.serviceError : t.serviceDown)}
        </div>
      </div>
      {service.latency_ms != null && (
        <div className="text-xs text-slate-600 mt-1">{service.latency_ms}ms</div>
      )}
    </div>
  )
}

function StatCard({ label, value, color = 'slate', sub }) {
  const colors = {
    blue: 'text-blue-400',
    purple: 'text-purple-400',
    green: 'text-green-400',
    orange: 'text-orange-400',
    white: 'text-white',
    slate: 'text-slate-300',
  }
  return (
    <div className="rounded-lg p-4 bg-slate-900 border border-slate-800 text-center">
      <div className={`text-2xl font-bold ${colors[color] || colors.slate} mb-1`}>{value}</div>
      <div className="text-xs text-slate-500">{label}</div>
      {sub && <div className="text-xs text-slate-600 mt-0.5">{sub}</div>}
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
      <h1 className="text-xl font-bold text-slate-100 mb-6">{t.adminDashboard}</h1>

      {loading && <div className="text-slate-500 text-sm">{t.loading}</div>}

      {data && (
        <>
          <section className="mb-6">
            <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">{t.adminServicesTitle}</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {data.services.map(sv => <ServiceCard key={sv.name} service={sv} t={t} />)}
            </div>
          </section>

          <section className="mb-6">
            <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">{t.adminStatsTitle}</h2>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
              <StatCard label={t.statMessages}     value={s.messages_today}   color="blue" />
              <StatCard label={t.statPipelines}    value={s.pipelines_completed} color="white" />
              <StatCard label={t.statImages}       value={s.images_generated}  color="purple" />
              <StatCard label={t.statUsersActive}  value={s.users_active}      color="green" />
              {s.users_locked > 0 && (
                <StatCard label={t.statUsersLocked} value={s.users_locked} color="orange" />
              )}
              <StatCard
                label="API Keys"
                value={`${s.api_keys_configured}/${s.api_keys_total}`}
                color={s.api_keys_configured === s.api_keys_total ? 'green' : 'orange'}
              />
              {s.ram && (
                <StatCard
                  label="RAM"
                  value={`${s.ram.percent}%`}
                  color={s.ram.percent > 85 ? 'orange' : 'slate'}
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
