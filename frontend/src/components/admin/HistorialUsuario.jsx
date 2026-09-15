import { useState, useEffect } from 'react'
import { useI18n, localeFor } from '../../i18n/index.jsx'
import api from '../../api/client'
import { useJaxStore } from '../../store/useJaxStore'
import { mensajeDeError } from '../../pages/admin/erroresAdmin'

// Historial corto de un usuario (2026-09-15, admin usuarios etapa 3): las
// últimas 50 acciones de administración sobre él, la más nueva primero. La
// fecha sigue el idioma activo (localeFor, I-1). Pinta solo con tokens.
export default function HistorialUsuario({ usuario, onCerrar }) {
  const { lang, t } = useI18n()
  const addToast = useJaxStore((s) => s.addToast)
  const [entradas, setEntradas] = useState(null)

  useEffect(() => {
    api.get(`/admin/users/${usuario.user_id}/audit`)
      .then((r) => setEntradas(r.data.entries))
      .catch((err) => {
        addToast({ type: 'error', message: mensajeDeError(t, err) })
        setEntradas([])
      })
  }, [usuario.user_id])

  function cambio(detalle) {
    if (!detalle || detalle.from === undefined) return null
    return `${detalle.from} → ${detalle.to}`
  }

  return (
    <div className="fixed inset-0 bg-fondo/70 flex items-center justify-center z-50">
      <div role="dialog" aria-modal="true" aria-labelledby="historial-titulo"
        className="bg-superficie border border-borde rounded-xl p-6 w-full max-w-lg shadow-2xl">
        <h2 id="historial-titulo" className="text-sm font-semibold text-texto mb-4">{t.adminHistoryTitle(usuario.email)}</h2>
        {entradas !== null && entradas.length === 0 && <p className="text-sm text-texto-tenue">{t.adminHistoryEmpty}</p>}
        <ul className="space-y-2 max-h-96 overflow-y-auto">
          {(entradas || []).map((e) => (
            <li key={e.id} className="text-xs border-b border-borde pb-2">
              <div className="flex justify-between gap-2">
                <span className="font-semibold text-texto">{t.adminAuditActions[e.action] || t.adminAuditUnknown}</span>
                <span className="text-texto-tenue">{e.ts ? new Date(e.ts).toLocaleString(localeFor(lang)) : ''}</span>
              </div>
              {cambio(e.detail) && <div className="text-texto">{cambio(e.detail)}</div>}
              <div className="text-texto-tenue">
                <span>{t.adminHistoryBy(e.actor_email || e.actor_user_id)}</span>
                {e.ip && <span> · {e.ip}</span>}
              </div>
            </li>
          ))}
        </ul>
        <div className="flex justify-end pt-4">
          <button type="button" onClick={onCerrar} className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors">{t.adminHistoryClose}</button>
        </div>
      </div>
    </div>
  )
}
