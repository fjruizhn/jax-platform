import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import { FACET_COLORS } from '../../store/useJaxStore'

function SimpleBarChart({ labels, datasets }) {
  const facets = Object.keys(datasets)
  if (!facets.length) return null

  const maxVal = Math.max(1, ...facets.flatMap(f => datasets[f]))

  return (
    <div className="overflow-x-auto">
      <div className="min-w-96">
        <div className="flex gap-1 items-end h-24">
          {labels.map((label, li) => (
            <div key={label} className="flex-1 flex flex-col items-center gap-0.5">
              <div className="w-full flex gap-0.5 items-end h-20">
                {facets.map(f => {
                  const val = datasets[f][li] || 0
                  const pct = (val / maxVal) * 100
                  return (
                    <div
                      key={f}
                      title={`${f}: ${val}`}
                      className="flex-1 rounded-t transition-all"
                      style={{
                        height: pct > 0 ? `${Math.max(pct, 5)}%` : '2px',
                        backgroundColor: FACET_COLORS[f] || '#94a3b8',
                        opacity: pct > 0 ? 1 : 0.2,
                      }}
                    />
                  )
                })}
              </div>
              <div className="text-xs text-slate-600 whitespace-nowrap">{label.slice(5)}</div>
            </div>
          ))}
        </div>
        <div className="flex flex-wrap gap-3 mt-3">
          {facets.map(f => (
            <div key={f} className="flex items-center gap-1.5">
              <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: FACET_COLORS[f] || '#94a3b8' }} />
              <span className="text-xs text-slate-400">{f}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default function AdminCosts() {
  const { t } = useI18n()
  const [period, setPeriod] = useState('day')
  const [data, setData] = useState(null)

  useEffect(() => {
    api.get(`/admin/usage?period=${period}`).then(r => setData(r.data)).catch(() => {})
  }, [period])

  const totalCost = data?.by_facet.reduce((s, r) => s + r.cost_usd, 0) || 0

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold text-slate-100">{t.adminCostsTitle}</h1>
        <div className="flex gap-1">
          {[['day', t.adminCostsDay], ['week', t.adminCostsWeek], ['month', t.adminCostsMonth]].map(([p, label]) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-3 py-1 rounded text-xs font-semibold transition-colors ${period === p ? 'bg-purple-600 text-white' : 'bg-slate-800 text-slate-400 hover:text-slate-200'}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {data?.by_facet.length > 0 ? (
        <>
          <div className="rounded-lg border border-slate-800 overflow-hidden mb-6">
            <table className="w-full text-sm">
              <thead className="bg-slate-900 border-b border-slate-800">
                <tr>
                  {[t.adminCostsFacet, t.adminCostsModel, t.adminCostsTokensIn, t.adminCostsTokensOut, t.adminCostsCost, t.adminCostsRequests].map(h => (
                    <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/50">
                {data.by_facet.map((r, i) => (
                  <tr key={i} className="bg-slate-900/50 hover:bg-slate-800/30 transition-colors">
                    <td className="px-4 py-3">
                      <span className="text-xs font-semibold px-2 py-0.5 rounded" style={{ color: FACET_COLORS[r.facet] || '#94a3b8', backgroundColor: (FACET_COLORS[r.facet] || '#94a3b8') + '20' }}>{r.facet}</span>
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-400 font-mono">{r.model}</td>
                    <td className="px-4 py-3 text-xs text-slate-300">{r.tokens_in.toLocaleString()}</td>
                    <td className="px-4 py-3 text-xs text-slate-300">{r.tokens_out.toLocaleString()}</td>
                    <td className="px-4 py-3 text-xs text-green-400 font-mono">${r.cost_usd.toFixed(6)}</td>
                    <td className="px-4 py-3 text-xs text-slate-300">{r.requests}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="bg-slate-900/80 border-t border-slate-800">
                <tr>
                  <td colSpan={4} className="px-4 py-3 text-xs font-semibold text-slate-400 uppercase">{t.adminCostsTotal}</td>
                  <td className="px-4 py-3 text-sm font-bold text-green-400 font-mono">${totalCost.toFixed(6)}</td>
                  <td className="px-4 py-3 text-xs text-slate-300">{data.by_facet.reduce((s, r) => s + r.requests, 0)}</td>
                </tr>
              </tfoot>
            </table>
          </div>

          {data.chart_data && (
            <div className="rounded-lg border border-slate-800 p-4 bg-slate-900/50">
              <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-4">{t.adminCostsChart}</h3>
              <SimpleBarChart labels={data.chart_data.labels} datasets={data.chart_data.datasets} />
            </div>
          )}
        </>
      ) : (
        <div className="text-center text-slate-600 text-sm py-12">{t.adminCostsNoData}</div>
      )}
    </div>
  )
}
