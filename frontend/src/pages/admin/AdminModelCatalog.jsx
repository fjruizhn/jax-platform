import { useState, useEffect, useCallback } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import { codigoDe, textoDeDetalleDeBinding, textoDeErrorDeBinding } from '../../api/errores'
import AlertaError from '../../components/AlertaError'
import FormContratoDispatch from './FormContratoDispatch'
import { TAMANO_BOTON_ACCION } from '../../tema/botones'

// El único rechazo del guard que se arregla declarando el contrato de la fila
// (PR-L). `modelo_de_otro_proveedor` no: su remedio es otro modelo o el PUT
// del binding.
const CODIGO_SIN_CONTRATO = 'modelo_sin_contrato_de_dispatch'

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

export default function AdminModelCatalog() {
  const { t } = useI18n()
  const [models, setModels] = useState([])
  const [proposals, setProposals] = useState([])
  const [syncing, setSyncing] = useState(false)
  const [syncError, setSyncError] = useState(false)
  // Task 3 (2026-09-15): lo que falló en un sync que respondió ok:false.
  const [syncFallidos, setSyncFallidos] = useState(null)
  const [deciding, setDeciding] = useState(null)
  // El error crudo: se traduce al renderizar, así un cambio de idioma lo sigue.
  const [decideError, setDecideError] = useState(null)
  // PR-L: opciones del parámetro (vienen del backend), la fila cuyo contrato
  // se está declarando y el aviso de éxito.
  const [opcionesParam, setOpcionesParam] = useState([])
  const [contratoDe, setContratoDe] = useState(null)
  const [contratoGuardado, setContratoGuardado] = useState(false)

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

  useEffect(() => { loadModels(); loadProposals() }, [loadModels, loadProposals])

  async function handleSync() {
    setSyncing(true)
    setSyncError(false)
    setSyncFallidos(null)
    try {
      // Solo escribe `model` — regla de oro (D1.3): nunca facet_binding.
      const { data } = await api.post('/admin/models/sync')
      // Task 3 (2026-09-15): antes se ignoraba el cuerpo y un sync en que
      // fallaba todo se veía como éxito. Lo que sí se sincronizó se recarga igual.
      if (data?.ok === false) {
        setSyncFallidos([...(data.providers_fallidos || []), ...(data.enrich_fallido ? ['models.dev'] : [])])
      }
      loadModels()
      loadProposals()
    } catch {
      setSyncError(true)
    } finally {
      setSyncing(false)
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

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-texto">{t.adminModelsTitle}</h2>
        <div className="flex items-center gap-2">
          {syncError && <span className="text-xs text-peligro">{t.adminModelsSyncError}</span>}
          {syncFallidos && <span role="alert" className="text-xs text-peligro">{t.sync_con_errores(syncFallidos.join(', '))}</span>}
          <button
            onClick={handleSync}
            disabled={syncing}
            className="text-xs px-3 py-1.5 rounded-lg bg-acento hover:bg-acento-hover text-sobre-color font-semibold disabled:opacity-50 transition-colors"
          >
            {syncing ? t.adminModelsSyncing : t.adminModelsSync}
          </button>
        </div>
      </div>

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
