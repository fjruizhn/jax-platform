import { useEffect, useState } from 'react'
import { useI18n, localeFor } from '../../i18n/index.jsx'
import api from '../../api/client'
import { textoDeErrorDeMesa } from '../../api/errores'
import AlertaError from '../../components/AlertaError'

const LIMITE = 50

// Administración → Pipelines ocultos (Task 7, spec
// 2026-09-22-descartar-pipelines §5): sólo superadmin -- la guardia del rol
// ya la pone /admin/* en App.jsx (RequireAuth + RequireSuperadmin), esta
// pantalla no repite esa comprobación. A diferencia de la pestaña
// Descartados (HistorialContenido.jsx), acá se listan los ocultos de TODOS
// los usuarios (GET /admin/pipelines/ocultos), no sólo los propios -- "un
// hidden no aparece en ninguna lista de su dueño: para él, ya no existe"
// (spec §4); esta es la ÚNICA vista que los muestra.
//
// Restaurar (POST /pipelines/{id}/restore) vuelve el pipeline a `discarded`
// -- es reversible (sigue en Descartados, donde su dueño lo puede recuperar
// de nuevo), así que no lleva ConfirmacionSuma ni Dialogo (spec Task 7 §1
// punto 2: "no lleva confirmación, porque es reversible").
export default function AdminPipelinesOcultos() {
  const { t, lang } = useI18n()
  const [lista, setLista] = useState([])
  const [hayMas, setHayMas] = useState(false)
  const [cargando, setCargando] = useState(false)
  const [error, setError] = useState(false)
  const [errorAccion, setErrorAccion] = useState(null)

  function cargar(offset) {
    setCargando(true)
    setError(false)
    return api.get('/admin/pipelines/ocultos', { params: { limite: LIMITE, offset } })
      .then(({ data }) => {
        const nuevos = Array.isArray(data?.pipelines) ? data.pipelines : []
        setLista((prev) => (offset === 0 ? nuevos : [...prev, ...nuevos]))
        setHayMas(data?.has_more === true)
      })
      .catch(() => setError(true))
      .finally(() => setCargando(false))
  }

  useEffect(() => {
    cargar(0)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function restaurar(pipelineId) {
    setErrorAccion(null)
    try {
      await api.post(`/pipelines/${pipelineId}/restore`)
      setLista((prev) => prev.filter((p) => p.pipeline_id !== pipelineId))
    } catch (e) {
      setErrorAccion(textoDeErrorDeMesa(t, e, t.restaurarError))
    }
  }

  return (
    <div>
      <h1 className="text-xl font-bold text-texto-fuerte mb-6">{t.pipelinesOcultosTitulo}</h1>

      {errorAccion && <AlertaError className="mb-4 text-sm">{errorAccion}</AlertaError>}

      {error && (
        <div className="mb-4 flex items-center gap-3">
          <AlertaError className="text-sm">{t.pipelinesOcultosError}</AlertaError>
          <button
            type="button"
            onClick={() => cargar(0)}
            className="px-3 py-1 rounded text-xs font-semibold bg-superficie text-texto-suave hover:text-texto transition-colors"
          >
            {t.historialRetry}
          </button>
        </div>
      )}

      {cargando && lista.length === 0 && !error && (
        <p className="text-sm text-texto-tenue">{t.historialLoading}</p>
      )}

      {!cargando && !error && lista.length === 0 && (
        <p className="text-sm text-texto-tenue text-center py-12">{t.sinOcultos}</p>
      )}

      {lista.length > 0 && (
        <div className="rounded-lg border border-borde overflow-hidden mb-4">
          <table className="w-full text-sm">
            <thead className="bg-hundido border-b border-borde">
              <tr>
                {[t.historialColName, t.pipelinesOcultosColFecha, ''].map((h, i) => (
                  <th key={i} className="text-left px-4 py-3 text-xs font-semibold text-texto-suave uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-borde/50">
              {lista.map((p) => (
                <tr key={p.pipeline_id} className="bg-hundido hover:bg-superficie transition-colors">
                  <td className="px-4 py-3">
                    <div className="text-texto">{p.name}</div>
                    <div className="text-xs text-texto-tenue">{t.ocultoDe(p.user_id)}</div>
                  </td>
                  <td data-campo="oculto" className="px-4 py-3 text-xs text-texto-tenue">
                    {typeof p.descartado_at === 'number' ? new Date(p.descartado_at * 1000).toLocaleString(localeFor(lang)) : '—'}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      type="button"
                      onClick={() => restaurar(p.pipeline_id)}
                      className="text-xs font-semibold text-acento-texto hover:underline"
                    >
                      {t.restaurarPipeline}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {hayMas && (
        <div className="text-center mb-6">
          <button
            type="button"
            onClick={() => cargar(lista.length)}
            disabled={cargando}
            className="px-4 py-1.5 rounded text-xs font-semibold bg-superficie text-texto-suave hover:text-texto transition-colors disabled:opacity-50"
          >
            {cargando ? t.historialLoadingMore : t.cargarMas}
          </button>
        </div>
      )}
    </div>
  )
}
