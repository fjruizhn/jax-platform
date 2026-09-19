import { useEffect } from 'react'
import { useJaxStore } from '../../store/useJaxStore'
import { useI18n, localeFor } from '../../i18n/index.jsx'
import { textoDeCausa } from '../../api/errores'
import AlertaError from '../AlertaError'
import DetallePipeline from './DetallePipeline'

// Status del pipeline (jax_engine/schemas.py / jacobs/models.py), leído con
// Object.hasOwn -- mismo criterio que textoDeStatus en RightPanel.jsx: un
// valor que esta versión no conoce va al texto genérico, nunca crudo.
function textoDeStatus(t, status) {
  return typeof status === 'string' && Object.hasOwn(t.pipelineStatusLabels, status)
    ? t.pipelineStatusLabels[status] : t.pipelineStatusDesconocido
}

// Cuerpo del Historial (Task 9, 2026-09-18) -- la lista + el detalle, SIN la
// cabecera de página (título, "Volver a Axioma"): eso es de cada lugar donde
// se monta, no del cuerpo.
//
// Ronda de arreglo 2 (2026-09-18, pedido de Fernando): extraído de
// pages/Historial.jsx para tener DOS lugares donde se monta con UNA sola
// implementación -- la ruta /historial (pages/Historial.jsx, con
// :pipelineId real en la URL) y la pestaña Repositorio → Pipelines
// (AdminRepository.jsx, sin ruta propia, con selección en estado local). El
// componente no sabe de routing: `pipelineId`/`nombreSeleccionado` y las dos
// acciones (elegir un pipeline, cerrar el detalle) las decide quien lo monta.
// Dos copias del mismo historial se desincronizan solas -- una sola fuente.
export default function HistorialContenido({ pipelineId, nombreSeleccionado, onSelect, onCloseDetail }) {
  const historial = useJaxStore((s) => s.historial)
  const cargarHistorial = useJaxStore((s) => s.cargarHistorial)
  const { t, lang } = useI18n()

  useEffect(() => {
    cargarHistorial()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const { pipelines, hasMore, cargando, error } = historial

  return (
    <>
      {/* Recomendado 3 (revisión final, 2026-09-18): el detalle va ANTES de
          la lista, no después. Los avisos de fin de pipeline (correo,
          Telegram) enlazan a /historial/:id -- con la lista completa arriba,
          había que bajar 50 filas (o 600) para ver el resultado al que el
          enlace mandaba. La lista sigue "ahí debajo", como fijan los tests
          de esta pantalla: no es una pantalla aparte, sólo cambia el orden. */}
      {pipelineId && (
        <div className="mb-6">
          <DetallePipeline
            pipelineId={pipelineId}
            nombre={nombreSeleccionado}
            onClose={onCloseDetail}
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
                      onClick={() => onSelect(p.pipeline_id, p.name)}
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
    </>
  )
}
