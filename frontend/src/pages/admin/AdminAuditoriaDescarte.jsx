import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { localeFor, useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'

const TIPOS = ['PIPELINE_DISCARDED', 'PIPELINE_RECOVERED', 'PIPELINE_HIDDEN', 'PIPELINE_RESTORED']

export default function AdminAuditoriaDescarte() {
  const { t, lang } = useI18n()
  const [filtros, setFiltros] = useState({ evento: '', pipeline_id: '', desde: '', hasta: '' })
  const [aplicados, setAplicados] = useState(filtros)
  const [eventos, setEventos] = useState([])
  const [cursor, setCursor] = useState(null)
  const [hayMas, setHayMas] = useState(false)
  const [cargando, setCargando] = useState(false)
  const [error, setError] = useState(false)
  const epoch = useRef(0)

  function cargar({ mas = false, filtrosCarga = aplicados } = {}) {
    const actual = ++epoch.current
    const vigente = () => actual === epoch.current
    const params = { limite: 50 }
    for (const key of ['evento', 'pipeline_id', 'desde', 'hasta']) {
      if (filtrosCarga[key]) params[key] = filtrosCarga[key]
    }
    if (mas && cursor) params.cursor = cursor
    setCargando(true)
    setError(false)
    return api.get('/admin/auditoria-descarte', { params })
      .then(({ data }) => {
        if (!vigente()) return
        const filas = Array.isArray(data?.eventos) ? data.eventos : []
        setEventos((prev) => (mas ? [...prev, ...filas] : filas))
        setCursor(typeof data?.cursor_siguiente === 'string' ? data.cursor_siguiente : null)
        setHayMas(data?.has_more === true)
      })
      .catch(() => { if (vigente()) setError(true) })
      .finally(() => { if (vigente()) setCargando(false) })
  }

  useEffect(() => { cargar({ filtrosCarga: aplicados }) }, [aplicados]) // eslint-disable-line react-hooks/exhaustive-deps

  function cambiar(key, value) { setFiltros((prev) => ({ ...prev, [key]: value })) }
  function aplicar(event) { event.preventDefault(); setAplicados({ ...filtros }) }
  const fecha = (ts) => new Date(ts * 1000).toLocaleString(localeFor(lang), { dateStyle: 'medium', timeStyle: 'short' })

  return (
    <section className="space-y-5">
      <h1 className="text-xl font-bold text-texto-fuerte">{t.adminAuditTitle}</h1>
      <form onSubmit={aplicar} className="grid grid-cols-1 gap-3 rounded border border-borde bg-superficie p-4 sm:grid-cols-2 lg:grid-cols-5">
        <label className="space-y-1 text-sm text-texto-suave">
          <span>{t.adminAuditEvent}</span>
          <select value={filtros.evento} onChange={(e) => cambiar('evento', e.target.value)} className="w-full rounded border border-borde bg-fondo px-2 py-2 text-texto">
            <option value="">{t.adminAuditAllEvents}</option>
            {TIPOS.map((tipo) => <option key={tipo} value={tipo}>{t[`adminAuditType_${tipo}`]}</option>)}
          </select>
        </label>
        <label className="space-y-1 text-sm text-texto-suave">
          <span>{t.adminAuditPipelineId}</span>
          <input value={filtros.pipeline_id} onChange={(e) => cambiar('pipeline_id', e.target.value)} maxLength={36} className="w-full rounded border border-borde bg-fondo px-2 py-2 text-texto" />
        </label>
        <label className="space-y-1 text-sm text-texto-suave">
          <span>{t.adminAuditFrom}</span>
          <input aria-label={t.adminAuditFrom} type="date" value={filtros.desde} onChange={(e) => cambiar('desde', e.target.value)} className="w-full rounded border border-borde bg-fondo px-2 py-2 text-texto" />
        </label>
        <label className="space-y-1 text-sm text-texto-suave">
          <span>{t.adminAuditTo}</span>
          <input aria-label={t.adminAuditTo} type="date" value={filtros.hasta} onChange={(e) => cambiar('hasta', e.target.value)} className="w-full rounded border border-borde bg-fondo px-2 py-2 text-texto" />
        </label>
        <button type="submit" disabled={cargando} className="self-end rounded bg-acento px-3 py-2 text-sm font-semibold text-sobre-color disabled:opacity-60">{t.adminAuditFilter}</button>
      </form>

      {error && <div role="alert" className="rounded border border-peligro-borde bg-peligro-fondo p-3 text-sm text-peligro">{t.adminAuditError} <button type="button" onClick={() => cargar()} className="ml-2 underline">{t.adminAuditRetry}</button></div>}
      {!error && !cargando && eventos.length === 0 && <p role="status" className="rounded border border-borde bg-superficie p-6 text-center text-texto-suave">{t.adminAuditEmpty}</p>}
      {eventos.length > 0 && <div className="overflow-x-auto rounded border border-borde">
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead className="bg-superficie text-xs uppercase text-texto-tenue"><tr>
            <th className="px-3 py-3">{t.adminAuditDate}</th><th className="px-3 py-3">{t.adminAuditEvent}</th><th className="px-3 py-3">{t.adminAuditPipeline}</th><th className="px-3 py-3">{t.adminAuditActor}</th><th className="px-3 py-3">{t.adminAuditReason}</th>
          </tr></thead>
          <tbody className="divide-y divide-borde bg-fondo">{eventos.map((e) => <tr key={e.id}>
            <td className="whitespace-nowrap px-3 py-3 text-texto-suave">{fecha(e.ts)}</td>
            <td className="px-3 py-3 text-texto">{t[`adminAuditType_${e.event_type}`] || e.event_type}</td>
            <td className="px-3 py-3"><Link className="text-acento-texto hover:underline" to={`/historial/${encodeURIComponent(e.pipeline_id)}`}>{e.pipeline_name || e.pipeline_id}</Link></td>
            <td className="px-3 py-3 text-texto-suave">{e.actor || t.adminAuditUnknown}</td>
            <td className="px-3 py-3 text-texto-suave">{e.motivo || '—'}</td>
          </tr>)}</tbody>
        </table>
      </div>}
      {hayMas && <div className="text-center"><button type="button" onClick={() => cargar({ mas: true })} disabled={cargando} className="rounded border border-borde bg-superficie px-4 py-2 text-sm text-texto hover:bg-fondo disabled:opacity-60">{cargando ? t.adminAuditLoading : t.adminAuditMore}</button></div>}
      {cargando && eventos.length === 0 && <p role="status" className="text-center text-sm text-texto-tenue">{t.adminAuditLoading}</p>}
    </section>
  )
}
