import { useState, useEffect, useCallback } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import { codigoDe, textoDeDetalleDeBinding, textoDeErrorDeBinding } from '../../api/errores'
import AlertaError from '../../components/AlertaError'
import FormContratoDispatch from './FormContratoDispatch'

// El único rechazo del guard que se arregla declarando el contrato de la fila
// (PR-L). `modelo_de_otro_proveedor` no: su remedio es otro modelo o el PUT
// del binding.
const CODIGO_SIN_CONTRATO = 'modelo_sin_contrato_de_dispatch'

const STATUS_COLOR = {
  available: 'text-green-400',
  degraded: 'text-yellow-400',
  deprecated: 'text-orange-400',
  gone: 'text-slate-500',
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
    try {
      // Solo escribe `model` — regla de oro (D1.3): nunca facet_binding.
      await api.post('/admin/models/sync')
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
        <h2 className="text-sm font-semibold text-slate-200">{t.adminModelsTitle}</h2>
        <div className="flex items-center gap-2">
          {syncError && <span className="text-xs text-red-400">{t.adminModelsSyncError}</span>}
          <button
            onClick={handleSync}
            disabled={syncing}
            className="text-xs px-3 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-700 text-white font-semibold disabled:opacity-50 transition-colors"
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
              className="ml-2 text-xs px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 text-slate-300 transition-colors"
            >
              {t.adminContratoDeclarar}
            </button>
          )}
        </AlertaError>
      )}

      {contratoGuardado && <p role="status" className="text-xs text-green-400 mb-3">{t.adminContratoGuardado}</p>}

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
        <div className="rounded-lg border border-purple-800/50 bg-purple-950/20 overflow-hidden mb-6">
          <div className="px-4 py-2 border-b border-purple-800/50">
            <h3 className="text-xs font-semibold text-purple-300 uppercase tracking-wider">{t.adminProposalsTitle}</h3>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-slate-900/60 border-b border-slate-800">
              <tr>
                {[t.adminProposalsFacet, t.adminProposalsProposed, t.adminProposalsReason, t.adminProposalsDetail, ''].map(h => (
                  <th key={h} className="text-left px-4 py-2 text-xs font-semibold text-slate-400 uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/50">
              {proposals.map(p => {
                const proposedModel = models.find(m => m.id === p.proposed_model_ref)
                return (
                  <tr key={p.id} className="bg-slate-900/50">
                    <td className="px-4 py-3 font-medium text-slate-200">{p.facet_key}</td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-300">
                      {proposedModel ? `${proposedModel.provider_id}/${proposedModel.model_id}` : p.proposed_model_ref}
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-400">{t[REASON_KEY[p.reason]] || p.reason}</td>
                    <td className="px-4 py-3 text-xs text-slate-500">
                      {p.detail}
                      {p.ultimo_rechazo && (
                        // Rastro del último 409 del guard (model_catalog_audit, PR-L).
                        <div className="mt-1 text-red-400">
                          <span>{t.adminProposalsUltimoRechazo(p.ultimo_rechazo.performed_at?.slice(0, 16) || t.adminModelsNoData)}</span>{' '}
                          <span>{textoDeDetalleDeBinding(t, p.ultimo_rechazo) || p.ultimo_rechazo.code}</span>
                          {p.ultimo_rechazo.code === CODIGO_SIN_CONTRATO && p.ultimo_rechazo.model_ref != null && (
                            <button
                              type="button"
                              onClick={() => abrirContrato(p.ultimo_rechazo.model_ref)}
                              className="ml-2 text-xs px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 text-slate-300 transition-colors"
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
                          className="text-xs px-2 py-1 rounded bg-green-900/40 hover:bg-green-800/50 text-green-300 disabled:opacity-40 transition-colors"
                        >
                          {deciding === `${p.id}-approve` ? t.adminProposalsApproving : t.adminProposalsApprove}
                        </button>
                        <button
                          onClick={() => decide(p.id, 'reject')}
                          disabled={!!deciding}
                          className="text-xs px-2 py-1 rounded bg-red-900/40 hover:bg-red-800/50 text-red-300 disabled:opacity-40 transition-colors"
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
        <p className="text-xs text-slate-500 mb-6">{t.adminProposalsEmpty}</p>
      )}

      <div className="rounded-lg border border-slate-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 border-b border-slate-800">
            <tr>
              {[t.adminModelsProvider, t.adminModelsModelId, t.adminModelsAlias, t.adminModelsStatus,
                t.adminModelsSource, t.adminModelsContext, t.adminModelsContrato, t.adminModelsPrice,
                t.adminModelsSourceCheckedAt].map(h => (
                <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/50">
            {models.map(m => (
              <tr key={m.id} className="bg-slate-900/50 hover:bg-slate-800/30 transition-colors">
                <td className="px-4 py-3 font-medium text-slate-200">{m.provider_id}</td>
                <td className="px-4 py-3 font-mono text-xs text-slate-300">{m.model_id}</td>
                <td className="px-4 py-3 text-xs text-slate-500">{m.is_alias ? '↪' : ''}</td>
                <td className={`px-4 py-3 text-xs font-semibold ${STATUS_COLOR[m.status] || 'text-slate-400'}`}>
                  {statusLabel(m.status)}
                  {m.consecutive_misses > 0 && <span className="text-slate-600"> ({m.consecutive_misses})</span>}
                </td>
                <td className="px-4 py-3 text-xs text-slate-500">{sourceLabel(m.source)}</td>
                <td className="px-4 py-3 text-xs text-slate-400">{m.context_window ?? t.adminModelsNoData}</td>
                <td className="px-4 py-3 text-xs">
                  {m.max_tokens_param && m.max_output_tokens != null
                    ? <span className="font-mono text-slate-400">{`${m.max_tokens_param} · ${m.max_output_tokens}`}</span>
                    : <span className="text-orange-400">{t.adminModelsContratoSinDeclarar}</span>}
                  <button
                    type="button"
                    onClick={() => abrirContrato(m.id)}
                    className="ml-2 text-xs px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 text-slate-300 transition-colors"
                  >
                    {t.adminContratoDeclarar}
                  </button>
                </td>
                <td className="px-4 py-3 text-xs text-slate-400 font-mono">
                  {m.price_input_per_1m_usd != null || m.price_output_per_1m_usd != null
                    ? `$${m.price_input_per_1m_usd ?? '?'} / $${m.price_output_per_1m_usd ?? '?'}`
                    : t.adminModelsNoData}
                </td>
                <td className="px-4 py-3 text-[10px] text-slate-600">{m.source_checked_at?.slice(0, 16) || t.adminModelsNoData}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
