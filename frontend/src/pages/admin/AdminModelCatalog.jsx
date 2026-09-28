import { useState, useEffect, useRef, useCallback } from 'react'
import { useI18n, localeFor } from '../../i18n/index.jsx'
import api from '../../api/client'
import { codigoDe, textoDeDetalleDeBinding, textoDeErrorDeBinding } from '../../api/errores'
import AlertaError from '../../components/AlertaError'
import FormContratoDispatch from './FormContratoDispatch'
import DialogoProgramacionSync from './DialogoProgramacionSync'
import { TAMANO_BOTON_ACCION } from '../../tema/botones'

// El único rechazo del guard que se arregla declarando el contrato de la fila
// (PR-L). `modelo_de_otro_proveedor` no: su remedio es otro modelo o el PUT
// del binding.
const CODIGO_SIN_CONTRATO = 'modelo_sin_contrato_de_dispatch'

// Polling de GET /admin/models/sync/estado mientras hay un sync corriendo
// (2026-09-27, pedido de Fernando: ver el avance real). 1s: suficientemente
// vivo para una barra de progreso, sin martillar el backend -- el mismo
// orden de magnitud que useEjecutor.js (INTERVALO_POLLING_MS).
const INTERVALO_POLLING_MS = 1000

const STATUS_COLOR = {
  available: 'text-exito',
  degraded: 'text-aviso',
  deprecated: 'text-obsoleto',
  gone: 'text-texto-tenue',
}

const REASON_KEY = {
  new_model_available: 'adminProposalReasonNewModel',
  drift_detected: 'adminProposalReasonDrift',
  deprecation_warning: 'adminProposalReasonDeprecation',
}

const ESTADO_LABEL_KEY = {
  ok: 'adminModelsUltimaActualizacionEstadoOk',
  con_problemas: 'adminModelsUltimaActualizacionEstadoConProblemas',
  error: 'adminModelsUltimaActualizacionEstadoError',
}

function mmss(iniciadoEn, ahora) {
  const segundos = Math.max(0, Math.floor((ahora - new Date(iniciadoEn).getTime()) / 1000))
  const m = String(Math.floor(segundos / 60)).padStart(2, '0')
  const s = String(segundos % 60).padStart(2, '0')
  return `${m}:${s}`
}

export default function AdminModelCatalog() {
  const { t, lang } = useI18n()
  const [models, setModels] = useState([])
  const [proposals, setProposals] = useState([])
  const [syncing, setSyncing] = useState(false)
  const [syncError, setSyncError] = useState(false)
  // Task 3 (2026-09-15): lo que falló en un sync que respondió ok:false.
  const [syncFallidos, setSyncFallidos] = useState(null)
  // 2026-09-27: un proveedor saltado (sin credencial, no alcanzable) y una
  // faceta 'primary' cuyo modelo dejó de estar disponible AHORA también
  // bajan `ok` (model_catalog.sync_all()) -- antes un saltado se veía como
  // éxito y nadie se enteraba (ver CONTEXT.md, caso real: anthropic bajo
  // jaxsvc).
  const [syncSaltados, setSyncSaltados] = useState(null)
  const [facetasEnRiesgo, setFacetasEnRiesgo] = useState(null)
  // MINOR-6 (cuarta auditoría adversarial, 2026-09-28): candado ocupado por
  // otro sync -- no es un error del catálogo, no se tocó nada.
  const [syncEnCurso, setSyncEnCurso] = useState(false)
  // Informativo, no es un error: `ok` puede seguir true con modelos nuevos.
  const [modelosNuevos, setModelosNuevos] = useState(null)
  const [deciding, setDeciding] = useState(null)
  // El error crudo: se traduce al renderizar, así un cambio de idioma lo sigue.
  const [decideError, setDecideError] = useState(null)
  // PR-L: opciones del parámetro (vienen del backend), la fila cuyo contrato
  // se está declarando y el aviso de éxito.
  const [opcionesParam, setOpcionesParam] = useState([])
  const [contratoDe, setContratoDe] = useState(null)
  const [contratoGuardado, setContratoGuardado] = useState(false)

  // 2026-09-27: avance real de la sincronización (corriendo o no, sea quien
  // sea que la haya lanzado) + última corrida terminada, siempre visible.
  const [progreso, setProgreso] = useState(null)
  const [ultima, setUltima] = useState(null)
  const [ahora, setAhora] = useState(Date.now())  // fuerza el re-render del reloj de "transcurrido"
  const [programando, setProgramando] = useState(false)
  const [configSync, setConfigSync] = useState(null)
  // MINOR-7 (auditoría adversarial, 2026-09-27): error de red mientras se
  // consulta el estado (inicial o durante el polling) -- se corta el
  // polling automático y se ofrece un botón, en vez de reintentar para
  // siempre en silencio o quedar pegado sin avisar.
  const [pollingError, setPollingError] = useState(false)
  const pollingRef = useRef(null)
  const tickRef = useRef(null)
  // MINOR-7: una respuesta en vuelo que llega DESPUÉS de desmontar no puede
  // crear un intervalo nuevo ni tocar estado -- `detenerPolling()` del
  // cleanup ya corrió antes de que esa respuesta llegue.
  const montadoRef = useRef(true)
  const idEsperadoRef = useRef(null)

  const loadModels = useCallback(() => {
    api.get('/admin/models').then(r => {
      setModels(r.data.models)
      setOpcionesParam(r.data.max_tokens_param_opciones || [])
    }).catch(() => {})
  }, [])

  function abrirContrato(modelRef) {
    setContratoGuardado(false)
    setContratoDe(modelRef)
  }

  function contratoDeclarado() {
    setContratoDe(null)
    setContratoGuardado(true)
    setDecideError(null)
    loadModels()
    loadProposals()
  }

  const modeloContrato = contratoDe != null ? models.find(m => m.id === contratoDe) : null
  const detalleDecideError = decideError?.response?.data?.detail

  const loadProposals = useCallback(() => {
    api.get('/admin/models/proposals?status=pending').then(r => setProposals(r.data.proposals)).catch(() => {})
  }, [])

  const detenerPolling = useCallback(() => {
    if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null }
    if (tickRef.current) { clearInterval(tickRef.current); tickRef.current = null }
  }, [])

  // Procesa el resultado FINAL de un sync (ya terminado, `ultima.resultado`
  // -- ver GET /admin/models/sync/estado). Misma lógica que antes leía la
  // respuesta directa de POST /sync (2026-09-27: ahora corre en segundo
  // plano; esto es lo que corresponde a "terminó").
  const procesarResultado = useCallback((data) => {
    setSyncFallidos(null)
    setSyncSaltados(null)
    setFacetasEnRiesgo(null)
    setModelosNuevos(null)
    if (data?.ok === false) {
      const fallidos = [...(data.providers_fallidos || []), ...(data.enrich_fallido ? ['models.dev'] : [])]
      // MINOR-6: nunca mostrar "fallaron: " con la lista vacía -- `ok`
      // también puede ser false sólo por saltados o por facetas en
      // riesgo, que ya tienen su propio mensaje más abajo.
      if (fallidos.length) setSyncFallidos(fallidos)
      if (data.providers_saltados?.length) setSyncSaltados(data.providers_saltados)
      if (data.facetas_en_riesgo?.length) {
        setFacetasEnRiesgo(data.facetas_en_riesgo.map(f => `${f.facet_key} (${f.status})`))
      }
    }
    // Informativo: independiente de `ok` -- un sync exitoso también puede traer novedades.
    const nuevosTotal = Object.values(data?.nuevos || {}).flat()
    if (nuevosTotal.length) setModelosNuevos(nuevosTotal)
    loadModels()
    loadProposals()
  }, [loadModels, loadProposals])

  // Un tick de GET /admin/models/sync/estado. Si hay algo corriendo, arranca
  // (o mantiene) el polling y el reloj de "transcurrido"; si no, y la
  // "última" es la que estábamos esperando, procesa su resultado y frena.
  const refrescarEstado = useCallback(async (idEsperado) => {
    idEsperadoRef.current = idEsperado
    try {
      const { data } = await api.get('/admin/models/sync/estado')
      // MINOR-7: una respuesta que llega después de desmontar no toca estado
      // ni arma un intervalo nuevo (el cleanup del efecto de montaje ya
      // corrió `detenerPolling()` -- crear uno acá lo dejaría vivo para
      // siempre, sin nadie que lo limpie).
      if (!montadoRef.current) return
      setPollingError(false)
      setUltima(data.ultima || null)
      if (data.corriendo) {
        setProgreso(data.corriendo)
        setSyncing(true)
        setAhora(Date.now())
        if (!pollingRef.current) {
          pollingRef.current = setInterval(() => refrescarEstado(idEsperado), INTERVALO_POLLING_MS)
        }
        if (!tickRef.current) {
          tickRef.current = setInterval(() => { if (montadoRef.current) setAhora(Date.now()) }, 1000)
        }
        return
      }
      // Nada corriendo: si la última es la que esperábamos (o no había
      // ninguna esperada -- carga inicial), terminó.
      detenerPolling()
      setSyncing(false)
      setProgreso(null)
      if (idEsperado == null || data.ultima?.id === idEsperado) {
        if (data.ultima?.resultado) procesarResultado(data.ultima.resultado)
      }
    } catch {
      // MINOR-7: un error de red no reintenta para siempre en silencio --
      // se corta el polling, se limpia el avance y se deja un botón
      // ("Reintentar") que retoma desde el mismo `idEsperado`.
      if (!montadoRef.current) return
      detenerPolling()
      setSyncing(false)
      setProgreso(null)
      setPollingError(true)
    }
  }, [detenerPolling, procesarResultado])

  function reintentarPolling() {
    setPollingError(false)
    refrescarEstado(idEsperadoRef.current)
  }

  useEffect(() => {
    montadoRef.current = true
    loadModels()
    loadProposals()
    refrescarEstado(null)
    return () => {
      montadoRef.current = false
      detenerPolling()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleSync() {
    setSyncing(true)
    setSyncError(false)
    setSyncEnCurso(false)
    try {
      // Solo escribe `model` — regla de oro (D1.3): nunca facet_binding.
      // 2026-09-27: corre en segundo plano -- esto sólo confirma que
      // arrancó (202) y devuelve el id de la ejecución.
      const { data } = await api.post('/admin/models/sync')
      await refrescarEstado(data.ejecucion_id)
    } catch (err) {
      if (err?.response?.status === 409 && err.response.data?.code === 'sync_en_curso') {
        // MINOR-6: candado ocupado por otro sync -- no se tocó nada, no es
        // "fallaron".
        setSyncEnCurso(true)
        setSyncing(false)
      } else {
        setSyncError(true)
        setSyncing(false)
      }
    }
  }

  async function decide(id, action) {
    setDeciding(`${id}-${action}`)
    setDecideError(null)
    try {
      await api.post(`/admin/models/proposals/${id}/${action}`)
      loadProposals()
      loadModels()
    } catch (err) {
      // 2026-09-14 (PR-J): el catch estaba vacío. Una aprobación rechazada por
      // contrato de dispatch (409) parecía un click que no hizo nada.
      setDecideError(err)
    } finally {
      setDeciding(null)
    }
  }

  function statusLabel(status) {
    return t[`adminModelsStatus${status.charAt(0).toUpperCase()}${status.slice(1)}`] || status
  }

  function sourceLabel(source) {
    const key = `adminModelsSource${source.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join('')}`
    return t[key] || source
  }

  async function abrirProgramacion() {
    try {
      const { data } = await api.get('/admin/models/sync/config')
      setConfigSync(data)
      setProgramando(true)
    } catch { /* el botón simplemente no abre; sin estado de error dedicado a propósito -- acción de bajo riesgo */ }
  }

  function programacionGuardada(data) {
    setConfigSync(data)
    setProgramando(false)
  }

  function textoUltimaActualizacion() {
    if (!ultima || !ultima.terminado_en) return t.adminModelsUltimaActualizacionNunca
    const fecha = new Date(ultima.terminado_en).toLocaleString(localeFor(lang))
    const quien = ultima.origen === 'manual'
      ? t.adminModelsUltimaActualizacionManual(ultima.iniciado_por_email || t.adminModelsNoData)
      : t.adminModelsUltimaActualizacionProgramada
    const estadoLabel = t[ESTADO_LABEL_KEY[ultima.estado]] || ultima.estado
    return `${t.adminModelsUltimaActualizacionTitulo} ${fecha} (${quien}, ${estadoLabel})`
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-sm font-semibold text-texto">{t.adminModelsTitle}</h2>
        <div className="flex items-center gap-2">
          {syncError && <span className="text-xs text-peligro">{t.adminModelsSyncError}</span>}
          {syncEnCurso && <span role="alert" className="text-xs text-peligro">{t.sync_en_curso}</span>}
          {syncFallidos && <span role="alert" className="text-xs text-peligro">{t.sync_con_errores(syncFallidos.join(', '))}</span>}
          {syncSaltados && <span role="alert" className="text-xs text-peligro">{t.sync_con_saltados(syncSaltados.join(', '))}</span>}
          {facetasEnRiesgo && <span role="alert" className="text-xs text-peligro">{t.sync_facetas_en_riesgo(facetasEnRiesgo.join(', '))}</span>}
          {modelosNuevos && <span className="text-xs text-exito">{t.sync_modelos_nuevos(modelosNuevos.join(', '))}</span>}
          <button
            type="button"
            onClick={abrirProgramacion}
            className="text-xs px-3 py-1.5 rounded-lg bg-superficie-2 text-texto hover:text-texto-fuerte transition-colors"
          >
            {t.adminModelsProgramacionAbrir}
          </button>
          <button
            onClick={handleSync}
            disabled={syncing}
            className="text-xs px-3 py-1.5 rounded-lg bg-acento hover:bg-acento-hover text-sobre-color font-semibold disabled:opacity-50 transition-colors"
          >
            {syncing ? t.adminModelsSyncing : t.adminModelsSync}
          </button>
        </div>
      </div>

      {progreso && (
        <div className="mb-3 rounded-lg border border-borde bg-hundido px-3 py-2">
          <div className="flex items-center justify-between text-xs text-texto-suave mb-1">
            <span>{t.adminModelsSyncProgreso(progreso.paso_actual, progreso.pasos_total, progreso.detalle_paso)}</span>
            <span className="text-texto-tenue">{t.adminModelsSyncTranscurrido(mmss(progreso.iniciado_en, ahora))}</span>
          </div>
          <div className="h-1.5 w-full rounded-full bg-superficie-2 overflow-hidden">
            <div
              className="h-full bg-acento transition-all"
              style={{ width: `${Math.min(100, (progreso.paso_actual / Math.max(1, progreso.pasos_total)) * 100)}%` }}
            />
          </div>
        </div>
      )}

      {pollingError && (
        <div role="alert" className="mb-3 flex items-center justify-between rounded-lg border border-peligro-borde bg-peligro-fondo px-3 py-2 text-xs text-peligro">
          <span>{t.adminModelsSyncPollingError}</span>
          <button
            type="button"
            onClick={reintentarPolling}
            className={`ml-2 ${TAMANO_BOTON_ACCION} rounded bg-superficie-2 text-texto hover:text-texto-fuerte transition-colors`}
          >
            {t.adminModelsSyncReintentar}
          </button>
        </div>
      )}

      <p className="text-xs text-texto-tenue mb-4">{textoUltimaActualizacion()}</p>

      {programando && configSync && (
        <DialogoProgramacionSync
          config={configSync}
          onGuardado={programacionGuardada}
          onCerrar={() => setProgramando(false)}
        />
      )}

      {decideError && (
        <AlertaError className="text-xs mb-3">
          {textoDeErrorDeBinding(t, decideError) || t.adminProposalsDecideError}
          {codigoDe(decideError) === CODIGO_SIN_CONTRATO && detalleDecideError?.model_ref != null && (
            <button
              type="button"
              onClick={() => abrirContrato(detalleDecideError.model_ref)}
              className={`ml-2 ${TAMANO_BOTON_ACCION} rounded bg-superficie-2 text-texto hover:text-texto-fuerte transition-colors`}
            >
              {t.adminContratoDeclarar}
            </button>
          )}
        </AlertaError>
      )}

      {contratoGuardado && <p role="status" className="text-xs text-exito mb-3">{t.adminContratoGuardado}</p>}

      {modeloContrato && (
        <FormContratoDispatch
          key={modeloContrato.id}
          modelo={modeloContrato}
          opciones={opcionesParam}
          onGuardado={contratoDeclarado}
          onCancelar={() => setContratoDe(null)}
        />
      )}

      {proposals.length > 0 && (
        <div className="rounded-lg border border-acento/40 bg-acento-fondo overflow-hidden mb-6">
          <div className="px-4 py-2 border-b border-acento/40">
            <h3 className="text-xs font-semibold text-acento-texto uppercase tracking-wider">{t.adminProposalsTitle}</h3>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-hundido border-b border-borde">
              <tr>
                {[t.adminProposalsFacet, t.adminProposalsProposed, t.adminProposalsReason, t.adminProposalsDetail, ''].map(h => (
                  <th key={h} className="text-left px-4 py-2 text-xs font-semibold text-texto-suave uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-borde/50">
              {proposals.map(p => {
                const proposedModel = models.find(m => m.id === p.proposed_model_ref)
                return (
                  <tr key={p.id} className="bg-hundido">
                    <td className="px-4 py-3 font-medium text-texto">{p.facet_key}</td>
                    <td className="px-4 py-3 font-mono text-xs text-texto">
                      {proposedModel ? `${proposedModel.provider_id}/${proposedModel.model_id}` : p.proposed_model_ref}
                    </td>
                    <td className="px-4 py-3 text-xs text-texto-suave">{t[REASON_KEY[p.reason]] || p.reason}</td>
                    <td className="px-4 py-3 text-xs text-texto-tenue">
                      {p.detail}
                      {p.ultimo_rechazo && (
                        // Rastro del último 409 del guard (model_catalog_audit, PR-L).
                        <div className="mt-1 text-peligro">
                          <span>{t.adminProposalsUltimoRechazo(p.ultimo_rechazo.performed_at?.slice(0, 16) || t.adminModelsNoData)}</span>{' '}
                          <span>{textoDeDetalleDeBinding(t, p.ultimo_rechazo) || p.ultimo_rechazo.code}</span>
                          {p.ultimo_rechazo.code === CODIGO_SIN_CONTRATO && p.ultimo_rechazo.model_ref != null && (
                            <button
                              type="button"
                              onClick={() => abrirContrato(p.ultimo_rechazo.model_ref)}
                              className={`ml-2 ${TAMANO_BOTON_ACCION} rounded bg-superficie-2 text-texto hover:text-texto-fuerte transition-colors`}
                            >
                              {t.adminContratoDeclarar}
                            </button>
                          )}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => decide(p.id, 'approve')}
                          disabled={!!deciding}
                          className={`${TAMANO_BOTON_ACCION} rounded bg-exito-fondo text-exito border border-transparent hover:border-exito-borde disabled:opacity-40 transition-colors`}
                        >
                          {deciding === `${p.id}-approve` ? t.adminProposalsApproving : t.adminProposalsApprove}
                        </button>
                        <button
                          onClick={() => decide(p.id, 'reject')}
                          disabled={!!deciding}
                          className={`${TAMANO_BOTON_ACCION} rounded bg-peligro-fondo text-peligro border border-transparent hover:border-peligro-borde disabled:opacity-40 transition-colors`}
                        >
                          {deciding === `${p.id}-reject` ? t.adminProposalsRejecting : t.adminProposalsReject}
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {proposals.length === 0 && (
        <p className="text-xs text-texto-tenue mb-6">{t.adminProposalsEmpty}</p>
      )}

      <div className="rounded-lg border border-borde overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-hundido border-b border-borde">
            <tr>
              {[t.adminModelsProvider, t.adminModelsModelId, t.adminModelsAlias, t.adminModelsStatus,
                t.adminModelsSource, t.adminModelsContext, t.adminModelsContrato, t.adminModelsPrice,
                t.adminModelsSourceCheckedAt].map(h => (
                <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-texto-suave uppercase tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-borde/50">
            {models.map(m => (
              <tr key={m.id} className="bg-hundido hover:bg-superficie transition-colors">
                <td className="px-4 py-3 font-medium text-texto">{m.provider_id}</td>
                <td className="px-4 py-3 font-mono text-xs text-texto">{m.model_id}</td>
                <td className="px-4 py-3 text-xs text-texto-tenue">{m.is_alias ? '↪' : ''}</td>
                <td className={`px-4 py-3 text-xs font-semibold ${STATUS_COLOR[m.status] || 'text-texto-suave'}`}>
                  {statusLabel(m.status)}
                  {m.consecutive_misses > 0 && <span className="text-texto-tenue"> ({m.consecutive_misses})</span>}
                </td>
                <td className="px-4 py-3 text-xs text-texto-tenue">{sourceLabel(m.source)}</td>
                <td className="px-4 py-3 text-xs text-texto-suave">{m.context_window ?? t.adminModelsNoData}</td>
                <td className="px-4 py-3 text-xs">
                  {m.max_tokens_param && m.max_output_tokens != null
                    ? <span className="font-mono text-texto-suave">{`${m.max_tokens_param} · ${m.max_output_tokens}`}</span>
                    : <span className="text-aviso">{t.adminModelsContratoSinDeclarar}</span>}
                  <button
                    type="button"
                    onClick={() => abrirContrato(m.id)}
                    className={`ml-2 ${TAMANO_BOTON_ACCION} rounded bg-superficie-2 text-texto hover:text-texto-fuerte transition-colors`}
                  >
                    {t.adminContratoDeclarar}
                  </button>
                </td>
                <td className="px-4 py-3 text-xs text-texto-suave font-mono">
                  {m.price_input_per_1m_usd != null || m.price_output_per_1m_usd != null
                    ? `$${m.price_input_per_1m_usd ?? '?'} / $${m.price_output_per_1m_usd ?? '?'}`
                    : t.adminModelsNoData}
                </td>
                <td className="px-4 py-3 text-[10px] text-texto-tenue">{m.source_checked_at?.slice(0, 16) || t.adminModelsNoData}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
