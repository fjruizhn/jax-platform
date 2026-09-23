import { useEffect, useState } from 'react'
import { useI18n, localeFor } from '../../i18n/index.jsx'
import api from '../../api/client'
import { textoDeErrorDeMesa } from '../../api/errores'
import AlertaError from '../../components/AlertaError'
import ConfirmacionSuma from '../../components/ConfirmacionSuma'
import { TAMANO_BOTON_ACCION } from '../../tema/botones'

const LIMITE = 50

// Administración → Pipelines descartados y ocultos (Task 7, spec
// 2026-09-22-descartar-pipelines §5; pestaña "Descartados" agregada
// 2026-09-22 al cerrar los dos huecos de la revisión final de la rama).
//
// "Un hidden no aparece en ninguna lista de su dueño: para él, ya no
// existe" (spec §4) -- la pestaña Ocultos es la ÚNICA vista que los
// muestra, y sólo al superadmin. La pestaña Descartados es su equivalente
// para 'discarded': el superadmin ya podía ocultar/restaurar el
// descartado de OTRO usuario, pero no tenía forma de VERLO si no era él
// quien lo había descartado (GET /api/pipelines?estado=discarded filtra
// por el usuario del token) -- sin esta pestaña, un superadmin que
// restauraba el oculto de otro no podía volver a ocultarlo desde la UI.
//
// Restaurar (POST /pipelines/{id}/restore) vuelve el pipeline a `discarded`
// -- es reversible (sigue en Descartados, donde su dueño lo puede recuperar
// de nuevo), así que no lleva ConfirmacionSuma ni Dialogo (spec Task 7 §1
// punto 2: "no lleva confirmación, porque es reversible"). Ocultar (POST
// /pipelines/{id}/hide) SÍ lleva ConfirmacionSuma -- mismo criterio que
// "Borrar" en la pestaña Descartados del propio historial
// (HistorialContenido.jsx): deja de verse en cualquier lista salvo esta.
export default function AdminPipelinesOcultos() {
  const { t, lang } = useI18n()
  const [tab, setTab] = useState('descartados')

  const [listaOcultos, setListaOcultos] = useState([])
  const [hayMasOcultos, setHayMasOcultos] = useState(false)
  const [cargandoOcultos, setCargandoOcultos] = useState(false)
  const [errorOcultos, setErrorOcultos] = useState(false)
  const [errorAccionOcultos, setErrorAccionOcultos] = useState(null)

  const [listaDescartados, setListaDescartados] = useState([])
  const [hayMasDescartados, setHayMasDescartados] = useState(false)
  const [cargandoDescartados, setCargandoDescartados] = useState(false)
  const [errorDescartados, setErrorDescartados] = useState(false)
  const [errorAccionDescartados, setErrorAccionDescartados] = useState(null)
  const [aOcultar, setAOcultar] = useState(null)

  // Invalidación explícita (fix round 1, BLOCK-1, revisión adversarial de
  // PR 151): "la lista está vacía" NO es la señal de "hay que recargar" --
  // ese idioma es la trampa (LAS CUATRO #2: todo caché declara su
  // invalidación en el MISMO commit que lo crea). Escenario que probó el
  // revisor: A y B descartados, C oculto. Se abre Descartados (carga A,B),
  // se va a Ocultos (carga C), se Restaura C, se vuelve a Descartados -> C
  // no aparece, porque `listaDescartados` NO está vacía (sigue teniendo A y
  // B) y la pestaña nunca se marcó para recargar. Cada bandera arranca en
  // `true` (la primera vez que se visita cada pestaña SIEMPRE carga) y cada
  // acción que puede cambiar lo que la OTRA pestaña necesita mostrar la
  // vuelve a poner en `true`: Ocultar invalida Ocultos (el pipeline pasa a
  // 'hidden', antes no estaba ahí); Restaurar invalida Descartados (pasa de
  // 'hidden' a 'discarded' -- exactamente el escenario del revisor);
  // Recuperar invalida Descartados también (por la misma disciplina, aunque
  // ya se filtra la fila local: no depender de que el filtrado optimista
  // sea la única fuente de verdad si la pestaña se vuelve a visitar).
  const [necesitaRecargaOcultos, setNecesitaRecargaOcultos] = useState(true)
  const [necesitaRecargaDescartados, setNecesitaRecargaDescartados] = useState(true)

  function cargarOcultos(offset) {
    setCargandoOcultos(true)
    setErrorOcultos(false)
    return api.get('/admin/pipelines/ocultos', { params: { limite: LIMITE, offset } })
      .then(({ data }) => {
        const nuevos = Array.isArray(data?.pipelines) ? data.pipelines : []
        setListaOcultos((prev) => (offset === 0 ? nuevos : [...prev, ...nuevos]))
        setHayMasOcultos(data?.has_more === true)
      })
      .catch(() => {
        setErrorOcultos(true)
        // MINOR-A (fix round 2, revisión adversarial de PR 151): la
        // bandera de invalidación se pone en `false` ANTES de este pedido
        // (efecto de arriba) -- si el pedido falla, sin esto la lista
        // vieja/vacía se queda mostrando y cambiar de pestaña y volver NO
        // reintenta (sólo el botón "Reintentar" manual lo haría). Se
        // restaura acá, en el único lugar que sabe que la carga falló de
        // verdad, sin importar quién la disparó (el efecto automático o
        // el propio botón de reintentar).
        setNecesitaRecargaOcultos(true)
      })
      .finally(() => setCargandoOcultos(false))
  }

  function cargarDescartados(offset) {
    setCargandoDescartados(true)
    setErrorDescartados(false)
    return api.get('/admin/pipelines/descartados', { params: { limite: LIMITE, offset } })
      .then(({ data }) => {
        const nuevos = Array.isArray(data?.pipelines) ? data.pipelines : []
        setListaDescartados((prev) => (offset === 0 ? nuevos : [...prev, ...nuevos]))
        setHayMasDescartados(data?.has_more === true)
      })
      .catch(() => {
        setErrorDescartados(true)
        // Mismo motivo que cargarOcultos, arriba.
        setNecesitaRecargaDescartados(true)
      })
      .finally(() => setCargandoDescartados(false))
  }

  // Sólo depende de `tab` a propósito (fix round 1, hallazgo propio al
  // verificar BLOCK-1): las banderas de invalidación tienen que disparar la
  // recarga la PRÓXIMA VEZ que se visite la pestaña, no apenas se marcan --
  // si el efecto dependiera también de `necesitaRecargaDescartados`,
  // marcarla en `true` DESDE la propia pestaña Descartados (p. ej. al
  // Recuperar) dispararía una recarga INMEDIATA ahí mismo, que pisa el
  // filtrado local optimista con una respuesta de red que todavía no
  // refleja el cambio (medido: el test de Recuperar volvía a mostrar la
  // fila que se acababa de quitar). Los valores de las banderas siguen
  // frescos igual -- están en el mismo cierre por el re-render de React
  // antes de que el usuario pueda hacer otro click.
  useEffect(() => {
    if (tab === 'ocultos') {
      if (necesitaRecargaOcultos) {
        setNecesitaRecargaOcultos(false)
        cargarOcultos(0)
      }
    } else if (necesitaRecargaDescartados) {
      setNecesitaRecargaDescartados(false)
      cargarDescartados(0)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  async function restaurar(pipelineId) {
    setErrorAccionOcultos(null)
    try {
      await api.post(`/pipelines/${pipelineId}/restore`)
      setListaOcultos((prev) => prev.filter((p) => p.pipeline_id !== pipelineId))
      // Restaurar vuelve el pipeline a 'discarded': Descartados tiene que
      // volver a pedirse la próxima vez que se visite (ver el comentario de
      // arriba, BLOCK-1).
      setNecesitaRecargaDescartados(true)
    } catch (e) {
      setErrorAccionOcultos(textoDeErrorDeMesa(t, e, t.restaurarError))
    }
  }

  async function recuperar(pipelineId) {
    setErrorAccionDescartados(null)
    try {
      await api.post(`/pipelines/${pipelineId}/recover`)
      setListaDescartados((prev) => prev.filter((p) => p.pipeline_id !== pipelineId))
      // Misma disciplina de invalidación que restaurar/ocultar -- el
      // filtrado local ya corrige la vista actual, esto es para que una
      // vuelta a esta pestaña no dependa únicamente de ese filtrado
      // optimista como única fuente de verdad.
      setNecesitaRecargaDescartados(true)
    } catch (e) {
      setErrorAccionDescartados(textoDeErrorDeMesa(t, e, t.recuperarError))
    }
  }

  function abrirOcultar(p) {
    setErrorAccionDescartados(null)
    setAOcultar(p)
  }

  function cerrarOcultar() {
    setAOcultar(null)
    setErrorAccionDescartados(null)
  }

  async function confirmarOcultar() {
    const pipelineId = aOcultar.pipeline_id
    try {
      await api.post(`/pipelines/${pipelineId}/hide`)
      setListaDescartados((prev) => prev.filter((p) => p.pipeline_id !== pipelineId))
      // Ocultar vuelve el pipeline 'hidden': Ocultos tiene que volver a
      // pedirse la próxima vez que se visite (BLOCK-1, ver el comentario de
      // arriba) -- es exactamente el caso que el revisor probó.
      setNecesitaRecargaOcultos(true)
      setAOcultar(null)
    } catch (e) {
      setErrorAccionDescartados(textoDeErrorDeMesa(t, e, t.ocultarError))
      setAOcultar(null)
    }
  }

  function cambiarTab(id) {
    setErrorAccionOcultos(null)
    setErrorAccionDescartados(null)
    setTab(id)
  }

  return (
    <div>
      <h1 className="text-xl font-bold text-texto-fuerte mb-6">{t.pipelinesOcultosTitulo}</h1>

      <div className="flex gap-1 mb-4 border-b border-borde">
        {[
          { id: 'descartados', label: t.pestanaDescartados },
          { id: 'ocultos', label: t.pestanaOcultos },
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
          {errorAccionDescartados && <AlertaError className="mb-4 text-sm">{errorAccionDescartados}</AlertaError>}

          {errorDescartados && (
            <div className="mb-4 flex items-center gap-3">
              <AlertaError className="text-sm">{t.descartadosError}</AlertaError>
              <button
                type="button"
                onClick={() => cargarDescartados(0)}
                className="px-3 py-1 rounded text-xs font-semibold bg-superficie text-texto-suave hover:text-texto transition-colors"
              >
                {t.historialRetry}
              </button>
            </div>
          )}

          {cargandoDescartados && listaDescartados.length === 0 && !errorDescartados && (
            <p className="text-sm text-texto-tenue">{t.historialLoading}</p>
          )}

          {!cargandoDescartados && !errorDescartados && listaDescartados.length === 0 && (
            <p className="text-sm text-texto-tenue text-center py-12">{t.sinDescartados}</p>
          )}

          {listaDescartados.length > 0 && (
            <div className="rounded-lg border border-borde overflow-hidden mb-4">
              <table className="w-full text-sm">
                <thead className="bg-hundido border-b border-borde">
                  <tr>
                    {[t.historialColName, t.descartadosColFecha, ''].map((h, i) => (
                      <th key={i} className="text-left px-4 py-3 text-xs font-semibold text-texto-suave uppercase tracking-wider">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-borde/50">
                  {listaDescartados.map((p) => (
                    <tr key={p.pipeline_id} className="bg-hundido hover:bg-superficie transition-colors">
                      <td className="px-4 py-3">
                        <div className="text-texto">{p.name}</div>
                        <div className="text-xs text-texto-tenue">{t.ocultoDe(p.user_id)}</div>
                      </td>
                      <td data-campo="descartado" className="px-4 py-3 text-xs text-texto-tenue">
                        {typeof p.descartado_at === 'number' ? new Date(p.descartado_at * 1000).toLocaleString(localeFor(lang)) : '—'}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="flex justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => recuperar(p.pipeline_id)}
                            // MINOR (fix round 1, revisión adversarial de PR 151): sin
                            // TAMANO_BOTON_ACCION medía 16px de alto (line-height de
                            // text-xs, sin ningún py-*) -- bajo el mínimo WCAG 2.2
                            // §2.5.8 (24×24px). El detector de botonesConPocoRelleno
                            // no lo veía: declara "irresoluble" un botón SIN ninguna
                            // utilidad de padding vertical (podría ser un link inline
                            // dentro de una oración, la excepción "Inline" de la norma)
                            // -- éste no lo es, es un botón de acción real en una
                            // tabla, así que se le da el tamaño explícito en vez de
                            // dejarlo en el punto ciego declarado del detector.
                            className={`${TAMANO_BOTON_ACCION} font-semibold text-acento-texto hover:underline`}
                          >
                            {t.recuperarPipeline}
                          </button>
                          <button
                            type="button"
                            onClick={() => abrirOcultar(p)}
                            // Mismo motivo que el botón de arriba (Recuperar).
                            className={`${TAMANO_BOTON_ACCION} font-semibold text-peligro hover:underline`}
                          >
                            {t.ocultarPipeline}
                          </button>
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
                onClick={() => cargarDescartados(listaDescartados.length)}
                disabled={cargandoDescartados}
                className="px-4 py-1.5 rounded text-xs font-semibold bg-superficie text-texto-suave hover:text-texto transition-colors disabled:opacity-50"
              >
                {cargandoDescartados ? t.historialLoadingMore : t.cargarMas}
              </button>
            </div>
          )}

          {aOcultar && (
            <ConfirmacionSuma
              titulo={t.ocultarTitulo}
              mensaje={t.ocultarMensaje(aOcultar.name)}
              textoConfirmar={t.ocultarPipeline}
              onConfirmar={confirmarOcultar}
              onCancelar={cerrarOcultar}
            />
          )}
        </>
      ) : (
        <>
          {errorAccionOcultos && <AlertaError className="mb-4 text-sm">{errorAccionOcultos}</AlertaError>}

          {errorOcultos && (
            <div className="mb-4 flex items-center gap-3">
              <AlertaError className="text-sm">{t.pipelinesOcultosError}</AlertaError>
              <button
                type="button"
                onClick={() => cargarOcultos(0)}
                className="px-3 py-1 rounded text-xs font-semibold bg-superficie text-texto-suave hover:text-texto transition-colors"
              >
                {t.historialRetry}
              </button>
            </div>
          )}

          {cargandoOcultos && listaOcultos.length === 0 && !errorOcultos && (
            <p className="text-sm text-texto-tenue">{t.historialLoading}</p>
          )}

          {!cargandoOcultos && !errorOcultos && listaOcultos.length === 0 && (
            <p className="text-sm text-texto-tenue text-center py-12">{t.sinOcultos}</p>
          )}

          {listaOcultos.length > 0 && (
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
                  {listaOcultos.map((p) => (
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

          {hayMasOcultos && (
            <div className="text-center mb-6">
              <button
                type="button"
                onClick={() => cargarOcultos(listaOcultos.length)}
                disabled={cargandoOcultos}
                className="px-4 py-1.5 rounded text-xs font-semibold bg-superficie text-texto-suave hover:text-texto transition-colors disabled:opacity-50"
              >
                {cargandoOcultos ? t.historialLoadingMore : t.cargarMas}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
