import { useState, useEffect } from 'react'
import { useI18n, localeFor } from '../../i18n/index.jsx'
import api from '../../api/client'
import { colorToken, tokenDeFaceta } from '../../tema/tokens'

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
                        backgroundColor: colorToken(tokenDeFaceta(f)),
                        opacity: pct > 0 ? 1 : 0.2,
                      }}
                    />
                  )
                })}
              </div>
              <div className="text-xs text-texto-tenue whitespace-nowrap">{label.slice(5)}</div>
            </div>
          ))}
        </div>
        <div className="flex flex-wrap gap-3 mt-3">
          {facets.map(f => (
            <div key={f} className="flex items-center gap-1.5">
              <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: colorToken(tokenDeFaceta(f)) }} />
              <span className="text-xs text-texto-suave">{f}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default function AdminCosts() {
  const { t, lang } = useI18n()
  const [period, setPeriod] = useState('day')
  const [data, setData] = useState(null)

  useEffect(() => {
    api.get(`/admin/usage?period=${period}`).then(r => setData(r.data)).catch(() => {})
  }, [period])

  // cost_usd es null cuando el modelo no tiene precio en el catálogo (ver
  // record_usage en backend/api/admin/usage.py) — sumarlo como si fuera $0
  // maquillaría el total, así que se excluye de la suma y se marca como
  // parcial (unpriced_requests > 0 en algún grupo, o cost_usd null en
  // alguno) para que quede visible que el total no cubre todo el período.
  const totalCost = data?.by_facet.reduce((s, r) => s + (r.cost_usd || 0), 0) || 0
  const hasPartialTotal = data?.by_facet.some(r => r.cost_usd === null || r.unpriced_requests > 0) || false

  // Task 4a (2026-09-15, cola durable de uso): DOS estados, no uno. La
  // especificación es el docstring de registros_perdidos_stats() en
  // backend/api/admin/usage.py:
  //   - en_cola > 0            -> PENDIENTE: el total se completa solo cuando
  //                               drene el reintento. Aviso suave (info).
  //   - registros_perdidos > 0 -> PERDIDO: la fila no entró en la DB y tampoco
  //                               se pudo dejar en el respaldo.
  //   - perdidas_por_desborde  -> PERDIDO también, pero por el respaldo lleno
  //                               descartando lo más viejo. Se nombra distinto
  //                               porque la acción del admin es otra.
  // Los dos estados se pueden dar a la vez y entonces se muestran los dos.
  // `|| 0` porque un backend viejo (o el cableado de la Task 4b todavía sin
  // hacer) no manda estos campos: ausente es cero, no un aviso inventado.
  const enCola = data?.en_cola || 0
  const perdidos = data?.registros_perdidos || 0
  const desbordadas = data?.perdidas_por_desborde || 0
  const conteo = (n) => n.toLocaleString(localeFor(lang))

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold text-texto-fuerte">{t.adminCostsTitle}</h1>
        <div className="flex gap-1">
          {[['day', t.adminCostsDay], ['week', t.adminCostsWeek], ['month', t.adminCostsMonth]].map(([p, label]) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={`px-3 py-1 rounded text-xs font-semibold transition-colors ${period === p ? 'bg-acento text-sobre-color' : 'bg-superficie text-texto-suave hover:text-texto'}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Task 7 + Task 4a: filas que no llegaron a axioma_usage (por proceso,
          desde el arranque). Fuera del condicional de filas: si se perdieron
          todas, "sin datos" solo sería mentira. Primero lo irreversible. */}
      {perdidos > 0 && (
        <p role="status" className="text-xs font-semibold text-aviso bg-aviso-fondo border border-aviso-borde rounded px-3 py-2 mb-4">
          {t.adminCostsRegistrosPerdidos(perdidos, conteo(perdidos))}
        </p>
      )}
      {desbordadas > 0 && (
        <p role="status" className="text-xs font-semibold text-aviso bg-aviso-fondo border border-aviso-borde rounded px-3 py-2 mb-4">
          {t.adminCostsPerdidasPorDesborde(desbordadas, conteo(desbordadas))}
        </p>
      )}
      {enCola > 0 && (
        <div role="status" className="bg-info-fondo border border-info/40 rounded px-3 py-2 mb-4">
          <p className="text-xs text-info">{t.adminCostsEnCola(enCola, conteo(enCola))}</p>
          {/* Sin marca de vida del drenaje no se inventa una fecha: "hay 5
              pendientes" sin fecha no distingue una cola que avanza de un
              reintento muerto, y una fecha falsa lo taparía (Principio VIII). */}
          {data?.ultimo_reintento && (
            <p className="text-xs text-texto mt-0.5">
              {t.adminCostsUltimoReintento(new Date(data.ultimo_reintento).toLocaleString(localeFor(lang)))}
            </p>
          )}
        </div>
      )}

      {data?.by_facet.length > 0 ? (
        <>
          <div className="rounded-lg border border-borde overflow-hidden mb-6">
            <table className="w-full text-sm">
              <thead className="bg-hundido border-b border-borde">
                <tr>
                  {[t.adminCostsFacet, t.adminCostsModel, t.adminCostsTokensIn, t.adminCostsTokensOut, t.adminCostsCost, t.adminCostsRequests].map(h => (
                    <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-texto-suave uppercase tracking-wider">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-borde/50">
                {data.by_facet.map((r, i) => (
                  <tr key={i} className="bg-hundido hover:bg-superficie transition-colors">
                    <td className="px-4 py-3">
                      <span className="text-xs font-semibold px-2 py-0.5 rounded border bg-superficie"
                        style={{ color: colorToken(tokenDeFaceta(r.facet)), borderColor: colorToken(tokenDeFaceta(r.facet), 0.4) }}>{r.facet}</span>
                    </td>
                    <td className="px-4 py-3 text-xs text-texto-suave font-mono">{r.model}</td>
                    {/* Re-revisión acotada (2026-09-15, sin diferidos): sin locale,
                        estos números seguían el locale del navegador, no el idioma
                        activo de la app -- mismo defecto que I-1 en las fechas. */}
                    <td className="px-4 py-3 text-xs text-texto">{r.tokens_in.toLocaleString(localeFor(lang))}</td>
                    <td className="px-4 py-3 text-xs text-texto">{r.tokens_out.toLocaleString(localeFor(lang))}</td>
                    <td className="px-4 py-3 text-xs text-exito font-mono">
                      {r.cost_usd === null ? (
                        <span className="text-texto-tenue" title={t.adminCostsNoPricing}>{t.adminCostsNoPricing}</span>
                      ) : (
                        <>
                          ${r.cost_usd.toFixed(6)}
                          {r.unpriced_requests > 0 && (
                            <span title={t.adminCostsPartialNote}>{t.adminCostsPartialMarker}</span>
                          )}
                        </>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-texto">{r.requests}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="bg-hundido border-t border-borde">
                <tr>
                  <td colSpan={4} className="px-4 py-3 text-xs font-semibold text-texto-suave uppercase">{t.adminCostsTotal}</td>
                  <td className="px-4 py-3 text-sm font-bold text-exito font-mono" title={hasPartialTotal ? t.adminCostsPartialNote : undefined}>
                    {hasPartialTotal ? '~' : ''}${totalCost.toFixed(6)}{hasPartialTotal ? t.adminCostsPartialMarker : ''}
                  </td>
                  <td className="px-4 py-3 text-xs text-texto">{data.by_facet.reduce((s, r) => s + r.requests, 0)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
          {hasPartialTotal && (
            <p className="text-xs text-texto-tenue -mt-4 mb-6">{t.adminCostsPartialNote}</p>
          )}

          {data.chart_data && (
            <div className="rounded-lg border border-borde p-4 bg-hundido">
              <h3 className="text-xs font-semibold text-texto-suave uppercase tracking-wider mb-4">{t.adminCostsChart}</h3>
              <SimpleBarChart labels={data.chart_data.labels} datasets={data.chart_data.datasets} />
            </div>
          )}
        </>
      ) : (
        <div className="text-center text-texto-tenue text-sm py-12">{t.adminCostsNoData}</div>
      )}
    </div>
  )
}
