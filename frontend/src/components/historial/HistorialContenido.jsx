import { useEffect, useRef, useState } from 'react'
import { useJaxStore } from '../../store/useJaxStore'
import { useI18n, localeFor } from '../../i18n/index.jsx'
import { textoDeCausa, textoDeErrorDeMesa } from '../../api/errores'
import api from '../../api/client'
import AlertaError from '../AlertaError'
import ConfirmacionSuma from '../ConfirmacionSuma'
import DetallePipeline from './DetallePipeline'

const LIMITE_DESCARTADOS = 50

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
  const esSuperadmin = useJaxStore((s) => s.user?.role === 'superadmin')
  const { t, lang } = useI18n()

  useEffect(() => {
    cargarHistorial()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const { pipelines, hasMore, cargando, error } = historial

  // Pestaña Descartados (Task 6, spec 2026-09-22-descartar-pipelines §5):
  // estado LOCAL, propio -- no toca el store `historial`, porque los
  // descartados no son parte del historial principal (spec §5: "van a ser
  // muchos en el tiempo", vista propia y paginada).
  const [tab, setTab] = useState('todos')
  const [descartados, setDescartados] = useState([])
  const [hayMasDescartados, setHayMasDescartados] = useState(false)
  // Cursor de la página siguiente (2026-09-23): el backend lo devuelve en
  // `cursor_siguiente` y "Cargar más" lo manda tal cual -- cada página lee
  // ~50 filas en cualquier profundidad, en vez de `offset`, que lee y tira
  // todas las anteriores (docs/carga-descartados-cursor-2026-09-23.md).
  const [cursorDescartados, setCursorDescartados] = useState(null)
  const [cargandoDescartados, setCargandoDescartados] = useState(false)
  const [errorDescartados, setErrorDescartados] = useState(false)
  // Borrar (ocultar): sólo el superadmin, con ConfirmacionSuma -- spec §2.
  const [aBorrar, setABorrar] = useState(null)
  const [errorAccion, setErrorAccion] = useState(null)

  // `mas` = "Cargar más" (agrega); sin él, primera página (reemplaza).
  // Si la respuesta anterior no trajo cursor (un backend todavía sin él,
  // durante un despliegue), cae al `offset` = cantidad ya cargada.
  //
  // Época de la carga (2026-09-23): cada pedido toma un número nuevo y su
  // respuesta sólo se aplica si sigue siendo el ÚLTIMO. Sin esto, "Cargar
  // más" → cambiar de pestaña → volver (que recarga la primera página)
  // antes de que llegara la respuesta dejaba que la respuesta VIEJA se
  // agregara a la lista recién recargada (filas repetidas) y pisara su
  // cursor. "Cargar más" está deshabilitado mientras hay una carga en
  // curso, así que "el último pedido gana" no descarta nunca una página que
  // hacía falta.
  const epocaDescartados = useRef(0)
  function cargarDescartados({ mas = false } = {}) {
    const epoca = ++epocaDescartados.current
    const vigente = () => epoca === epocaDescartados.current
    setCargandoDescartados(true)
    setErrorDescartados(false)
    const params = { estado: 'discarded', limite: LIMITE_DESCARTADOS }
    if (mas && cursorDescartados) params.cursor = cursorDescartados
    else params.offset = mas ? descartados.length : 0
    return api.get('/pipelines', { params })
      .then(({ data }) => {
        if (!vigente()) return
        const nuevos = Array.isArray(data?.pipelines) ? data.pipelines : []
        setDescartados((prev) => (mas ? [...prev, ...nuevos] : nuevos))
        setHayMasDescartados(data?.has_more === true)
        setCursorDescartados(typeof data?.cursor_siguiente === 'string' ? data.cursor_siguiente : null)
      })
      .catch(() => { if (vigente()) setErrorDescartados(true) })
      .finally(() => { if (vigente()) setCargandoDescartados(false) })
  }

  useEffect(() => {
    if (tab === 'descartados') cargarDescartados()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  async function recuperar(pipelineId) {
    setErrorAccion(null)
    try {
      await api.post(`/pipelines/${pipelineId}/recover`)
      setDescartados((prev) => prev.filter((p) => p.pipeline_id !== pipelineId))
      // MAJOR-1 (fix round 1, revisión adversarial): un pipeline recuperado
      // vuelve a `aborted`/`expired` -- GET /pipelines (sin filtro) SÍ lo
      // trae de nuevo, pero el store `historial` sólo se pide una vez al
      // montar. Sin este refresco, "Todos" seguía sin el pipeline hasta que
      // algo MÁS disparara cargarHistorial() -- mismo patrón que
      // RightPanel.jsx::confirmarDescartar.
      await cargarHistorial()
    } catch (e) {
      // MINOR-4: texto genérico PROPIO (recuperarError) -- "no se pudo
      // descartar" mentiría sobre qué acción falló de verdad.
      setErrorAccion(textoDeErrorDeMesa(t, e, t.recuperarError))
    }
  }

  async function confirmarBorrar() {
    const pipelineId = aBorrar.pipeline_id
    try {
      await api.post(`/pipelines/${pipelineId}/hide`)
      setDescartados((prev) => prev.filter((p) => p.pipeline_id !== pipelineId))
      setABorrar(null)
    } catch (e) {
      // MINOR-4: texto genérico PROPIO (borrarError).
      setErrorAccion(textoDeErrorDeMesa(t, e, t.borrarError))
      setABorrar(null)
    }
  }

  // MINOR-1 (fix round 1): errorAccion sobrevivía al cierre del diálogo y al
  // cambio de pestaña, contaminando la acción siguiente -- se limpia acá, en
  // los tres disparadores (cerrar/cancelar, cambiar de pestaña, empezar una
  // acción nueva); recuperar() ya se limpia a sí misma arriba.
  function cambiarTab(id) {
    setErrorAccion(null)
    setTab(id)
  }

  function abrirBorrar(p) {
    setErrorAccion(null)
    setABorrar(p)
  }

  function cerrarBorrar() {
    setABorrar(null)
    setErrorAccion(null)
  }

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

      <div className="flex gap-1 mb-4 border-b border-borde">
        {[
          { id: 'todos', label: t.pestanaTodos },
          { id: 'descartados', label: t.pestanaDescartados },
        ].map(({ id, label }) => (
          <button
            key={id}
            type="button"
            onClick={() => cambiarTab(id)}
            className={`px-3 py-2 text-xs font-semibold uppercase tracking-wider transition-colors ${
              tab === id ? 'text-info border-b-2 border-info' : 'text-texto-tenue hover:text-texto'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'descartados' ? (
        <>
          {errorAccion && <AlertaError className="mb-4 text-sm">{errorAccion}</AlertaError>}

          {errorDescartados && (
            <div className="mb-4 flex items-center gap-3">
              <AlertaError className="text-sm">{t.descartadosError}</AlertaError>
              <button
                type="button"
                onClick={() => cargarDescartados()}
                className="px-3 py-1 rounded text-xs font-semibold bg-superficie text-texto-suave hover:text-texto transition-colors"
              >
                {t.historialRetry}
              </button>
            </div>
          )}

          {cargandoDescartados && descartados.length === 0 && !errorDescartados && (
            <p className="text-sm text-texto-tenue">{t.historialLoading}</p>
          )}

          {!cargandoDescartados && !errorDescartados && descartados.length === 0 && (
            <p className="text-sm text-texto-tenue text-center py-12">{t.sinDescartados}</p>
          )}

          {descartados.length > 0 && (
            <div className="rounded-lg border border-borde overflow-hidden mb-4">
              <table className="w-full text-sm">
                <thead className="bg-hundido border-b border-borde">
                  <tr>
                    {[t.historialColName, t.descartadosColFecha, t.historialColCost, ''].map((h, i) => (
                      <th key={i} className="text-left px-4 py-3 text-xs font-semibold text-texto-suave uppercase tracking-wider">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-borde/50">
                  {descartados.map((p) => (
                    <tr key={p.pipeline_id} className="bg-hundido hover:bg-superficie transition-colors">
                      <td className="px-4 py-3 text-texto">{p.name}</td>
                      <td data-campo="descartado" className="px-4 py-3 text-xs text-texto-tenue">
                        {typeof p.descartado_at === 'number' ? new Date(p.descartado_at * 1000).toLocaleString(localeFor(lang)) : '—'}
                      </td>
                      <td data-campo="costo" className="px-4 py-3 text-xs text-texto-tenue">
                        {typeof p.costo_usd === 'number' ? t.historialCost(p.costo_usd.toFixed(6)) : t.historialUnknown}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="flex justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => recuperar(p.pipeline_id)}
                            className="text-xs min-h-6 font-semibold text-acento-texto hover:underline"
                          >
                            {t.recuperarPipeline}
                          </button>
                          {esSuperadmin && (
                            <button
                              type="button"
                              onClick={() => abrirBorrar(p)}
                              className="text-xs min-h-6 font-semibold text-peligro hover:underline"
                            >
                              {t.borrarPipeline}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {hayMasDescartados && (
            <div className="text-center mb-6">
              <button
                type="button"
                onClick={() => cargarDescartados({ mas: true })}
                disabled={cargandoDescartados}
                className="px-4 py-1.5 rounded text-xs font-semibold bg-superficie text-texto-suave hover:text-texto transition-colors disabled:opacity-50"
              >
                {cargandoDescartados ? t.historialLoadingMore : t.cargarMas}
              </button>
            </div>
          )}

          {aBorrar && (
            <ConfirmacionSuma
              titulo={t.borrarTitulo}
              mensaje={t.borrarMensaje(aBorrar.name)}
              textoConfirmar={t.borrarPipeline}
              onConfirmar={confirmarBorrar}
              onCancelar={cerrarBorrar}
            />
          )}
        </>
      ) : (
      <>
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
                      className="text-xs min-h-6 font-semibold text-acento-texto hover:underline"
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
      )}
    </>
  )
}
