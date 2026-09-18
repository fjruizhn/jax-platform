import { useEffect } from 'react'
import { Link, useNavigate, useParams, useLocation } from 'react-router-dom'
import { useJaxStore } from '../store/useJaxStore'
import { useI18n, localeFor } from '../i18n/index.jsx'
import { useNombreDelSistema } from '../store/useApariencia'
import { textoDeCausa } from '../api/errores'
import AlertaError from '../components/AlertaError'
import DetallePipeline from '../components/historial/DetallePipeline'

// Status del pipeline (jax_engine/schemas.py / jacobs/models.py), leído con
// Object.hasOwn -- mismo criterio que textoDeStatus en RightPanel.jsx: un
// valor que esta versión no conoce va al texto genérico, nunca crudo.
function textoDeStatus(t, status) {
  return typeof status === 'string' && Object.hasOwn(t.pipelineStatusLabels, status)
    ? t.pipelineStatusLabels[status] : t.pipelineStatusDesconocido
}

// Task 9 (2026-09-18, historial-y-arreglos-de-pipeline): "un lugar... donde
// se listen los pipelines que se hicieron y se pueda leer el prompt que
// generaste" -- pedido textual de Fernando. GET /api/pipelines (Task 7) ya
// trae todo lo que esta pantalla necesita; sólo faltaba dónde mostrarlo.
//
// Ronda de arreglo 1 (2026-09-18): el detalle es la URL /historial/:pipelineId
// (App.jsx tiene las dos rutas, "/historial" y "/historial/:pipelineId",
// apuntando las dos acá) -- NO estado interno. Los avisos de fin de pipeline
// por correo y Telegram (otras dos tareas de la misma ronda,
// jax-platform/backend/aviso_pipeline.py:175 y jax/jacobs/aviso.py:80) arman
// el enlace como {origen}/historial/{pipeline_id} y esperan que abra ESE
// pipeline al entrar directo por la URL -- con estado interno no hay forma
// de que un link externo abra nada.
export default function Historial() {
  const historial = useJaxStore((s) => s.historial)
  const cargarHistorial = useJaxStore((s) => s.cargarHistorial)
  const { t, lang } = useI18n()
  const nombre = useNombreDelSistema(t)
  const navigate = useNavigate()
  const location = useLocation()
  const { pipelineId } = useParams()

  useEffect(() => {
    cargarHistorial()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const { pipelines, hasMore, cargando, error } = historial

  return (
    <div className="min-h-dvh bg-fondo text-texto p-6">
      <div className="max-w-5xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-xl font-bold text-texto-fuerte">{t.historialTitle}</h1>
          <Link to="/" className="text-xs text-texto-tenue hover:text-texto transition-colors flex items-center gap-1.5">
            <span>←</span>
            <span>{t.historialBack(nombre)}</span>
          </Link>
        </div>

        {/* Recomendado 3 (revisión final, 2026-09-18): el detalle va ANTES de
            la lista, no después. Los avisos de fin de pipeline (correo,
            Telegram) enlazan a /historial/:id -- con la lista completa
            arriba, había que bajar 50 filas (o 600) para ver el resultado al
            que el enlace mandaba. La lista sigue "ahí debajo", como ya fijan
            los tests de esta pantalla: no es una pantalla aparte, sólo
            cambia el orden. */}
        {pipelineId && (
          <div className="mb-6">
            <DetallePipeline
              pipelineId={pipelineId}
              nombre={location.state?.name}
              onClose={() => navigate('/historial')}
            />
          </div>
        )}

        {error && (
          <div className="mb-4 flex items-center gap-3">
            <AlertaError className="text-sm">{t.historialError}</AlertaError>
            <button
              type="button"
              onClick={() => cargarHistorial()}
              className="px-3 py-1 rounded text-xs font-semibold bg-superficie text-texto-suave hover:text-texto transition-colors"
            >
              {t.historialRetry}
            </button>
          </div>
        )}

        {cargando && pipelines.length === 0 && !error && (
          <p className="text-sm text-texto-tenue">{t.historialLoading}</p>
        )}

        {!cargando && !error && pipelines.length === 0 && (
          <p className="text-sm text-texto-tenue text-center py-12">{t.historialEmpty}</p>
        )}

        {pipelines.length > 0 && (
          <div className="rounded-lg border border-borde overflow-hidden mb-4">
            <table className="w-full text-sm">
              <thead className="bg-hundido border-b border-borde">
                <tr>
                  {[t.historialColName, t.historialColStatus, t.historialColCreated, t.historialColDuration, t.historialColCost, t.historialColCause, ''].map((h, i) => (
                    <th key={i} className="text-left px-4 py-3 text-xs font-semibold text-texto-suave uppercase tracking-wider">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-borde/50">
                {pipelines.map((p) => (
                  <tr key={p.pipeline_id} className="bg-hundido hover:bg-superficie transition-colors">
                    <td className="px-4 py-3 text-texto">{p.name}</td>
                    <td className="px-4 py-3 text-texto-suave">{textoDeStatus(t, p.status)}</td>
                    <td className="px-4 py-3 text-xs text-texto-tenue">
                      {typeof p.created_at === 'number' ? new Date(p.created_at * 1000).toLocaleString(localeFor(lang)) : '—'}
                    </td>
                    <td data-campo="duracion" className="px-4 py-3 text-xs text-texto">
                      {typeof p.duracion_s === 'number' ? t.historialDuration(p.duracion_s.toFixed(1)) : t.historialUnknown}
                    </td>
                    <td data-campo="costo" className="px-4 py-3 text-xs text-texto-tenue">
                      {typeof p.costo_usd === 'number' ? t.historialCost(p.costo_usd.toFixed(6)) : t.historialUnknown}
                    </td>
                    <td className="px-4 py-3 text-xs text-texto-tenue">{p.causa ? textoDeCausa(t, p.causa) : '—'}</td>
                    <td className="px-4 py-3 text-right">
                      <button
                        type="button"
                        onClick={() => navigate(`/historial/${p.pipeline_id}`, { state: { name: p.name } })}
                        className="text-xs font-semibold text-acento-texto hover:underline"
                      >
                        {t.historialViewDetail}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {hasMore && (
          <div className="text-center mb-6">
            <button
              type="button"
              onClick={() => cargarHistorial({ offset: pipelines.length })}
              disabled={cargando}
              className="px-4 py-1.5 rounded text-xs font-semibold bg-superficie text-texto-suave hover:text-texto transition-colors disabled:opacity-50"
            >
              {cargando ? t.historialLoadingMore : t.historialLoadMore}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
