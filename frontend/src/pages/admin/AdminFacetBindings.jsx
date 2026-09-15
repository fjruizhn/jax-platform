import { useState, useEffect, useCallback } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import { FACET_COLORS } from '../../store/useJaxStore'
import { codigoDe, textoDeDetalleDeBinding, textoDeErrorDeBinding } from '../../api/errores'
import AlertaError from '../../components/AlertaError'
import FormContratoDispatch from './FormContratoDispatch'

// El único rechazo del guard que se arregla declarando el contrato de la fila
// (PR-L ronda 1). `modelo_de_otro_proveedor` no: su remedio es otro modelo.
const CODIGO_SIN_CONTRATO = 'modelo_sin_contrato_de_dispatch'
const BOTON_SECUNDARIO = 'text-xs px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 text-slate-300 transition-colors'

// El 409 de contrato de dispatch trae un objeto en `detail` (2026-09-14,
// PR-J): antes se interpolaba tal cual y salía "[object Object]". Ese código
// se traduce; cualquier otro conserva el texto que mandó el backend.
function mensajeDeGuardado(t, err) {
  const traducido = textoDeErrorDeBinding(t, err)
  if (traducido) return traducido
  const detail = err?.response?.data?.detail
  return t.adminBindingsSaveError(typeof detail === 'string' ? detail : String(err))
}

const CAPABILITY_STYLE = {
  ok: 'text-green-400',
  warning: 'text-yellow-400',
  unknown: 'text-slate-500',
}

export default function AdminFacetBindings() {
  const { t } = useI18n()
  const [bindings, setBindings] = useState([])
  const [models, setModels] = useState([])
  const [editing, setEditing] = useState(null)
  const [selectedModelRef, setSelectedModelRef] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  // PR-L ronda 1: declarar el contrato de una fila desde el 409 del PUT o
  // desde el último rechazo guardado de la faceta. `contratoDe` guarda lo que
  // trae el rechazo (model_ref, proveedor, model_id): la fila puede no estar
  // en la lista cargada (status != available) y el formulario igual se abre.
  const [opcionesParam, setOpcionesParam] = useState([])
  const [contratoDe, setContratoDe] = useState(null)
  const [contratoGuardado, setContratoGuardado] = useState(false)

  const loadBindings = useCallback(() => {
    api.get('/admin/facet-bindings').then(r => setBindings(r.data.bindings)).catch(() => {})
  }, [])

  const loadModels = useCallback(() => {
    api.get('/admin/models?status=available').then(r => {
      setModels(r.data.models)
      setOpcionesParam(r.data.max_tokens_param_opciones || [])
    }).catch(() => {})
  }, [])

  useEffect(() => {
    loadBindings()
    loadModels()
  }, [loadBindings, loadModels])

  function abrirContrato(rechazo) {
    setContratoGuardado(false)
    setContratoDe({ model_ref: rechazo.model_ref, provider_id: rechazo.provider_modelo, model_id: rechazo.model_id })
  }

  function contratoDeclarado() {
    setContratoDe(null)
    setContratoGuardado(true)
    setSaveError(null)
    loadBindings()
    loadModels()
  }

  const modeloContrato = contratoDe && (
    models.find(m => m.id === contratoDe.model_ref) || {
      id: contratoDe.model_ref, provider_id: contratoDe.provider_id, model_id: contratoDe.model_id,
      max_tokens_param: null, max_output_tokens: null,
    }
  )

  function botonDeclarar(rechazo) {
    if (rechazo?.code !== CODIGO_SIN_CONTRATO || rechazo.model_ref == null) return null
    return (
      <button type="button" onClick={() => abrirContrato(rechazo)} className={`ml-2 ${BOTON_SECUNDARIO}`}>
        {t.adminContratoDeclarar}
      </button>
    )
  }

  function startEdit(b) {
    setEditing(b.facet_key)
    setSelectedModelRef(b.model_ref || '')
    setSaveError(null)
  }

  async function save(facetKey) {
    if (!selectedModelRef) return
    const model = models.find(m => m.id === Number(selectedModelRef))
    if (!model) return
    setSaving(true)
    setSaveError(null)
    try {
      await api.put(`/admin/facet-bindings/${facetKey}`, {
        provider_id: model.provider_id,
        model_ref: model.id,
      })
      setEditing(null)
      loadBindings()
    } catch (e) {
      // El error crudo: se traduce al renderizar (ver mensajeDeGuardado).
      setSaveError(e)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <h2 className="text-sm font-semibold text-slate-200 mb-4">{t.adminBindingsTitle}</h2>
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
      <div className="rounded-lg border border-slate-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 border-b border-slate-800">
            <tr>
              {[t.adminBindingsFacet, t.adminBindingsTransport, t.adminBindingsModel, t.adminBindingsCapability, ''].map(h => (
                <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/50">
            {bindings.map(b => (
              <tr key={b.facet_key} className="bg-slate-900/50 hover:bg-slate-800/30 transition-colors">
                <td className="px-4 py-3">
                  <span
                    className="text-xs font-semibold px-2 py-0.5 rounded"
                    style={{ color: FACET_COLORS[b.facet_key] || '#94a3b8', backgroundColor: (FACET_COLORS[b.facet_key] || '#94a3b8') + '20' }}
                  >
                    {b.display_name || b.facet_key}
                  </span>
                </td>
                <td className="px-4 py-3 text-xs text-slate-400 font-mono">{b.transport}</td>
                <td className="px-4 py-3">
                  {editing === b.facet_key ? (
                    <div className="flex items-center gap-1.5">
                      <select
                        value={selectedModelRef}
                        onChange={e => setSelectedModelRef(e.target.value)}
                        className="bg-slate-800 border border-slate-600 rounded-lg px-2 py-1 text-xs font-mono text-slate-200 focus:outline-none focus:border-purple-500"
                      >
                        <option value="">{t.adminBindingsSelectModel}</option>
                        {models.map(m => (
                          <option key={m.id} value={m.id}>{m.provider_id}/{m.model_id}</option>
                        ))}
                      </select>
                      <button
                        onClick={() => save(b.facet_key)}
                        disabled={saving || !selectedModelRef}
                        className="text-xs px-2 py-1 rounded bg-purple-600 hover:bg-purple-700 text-white font-semibold disabled:opacity-50 transition-colors"
                      >
                        {saving ? t.adminBindingsSaving : t.adminBindingsSave}
                      </button>
                      <button
                        onClick={() => setEditing(null)}
                        className="text-xs px-2 py-1 rounded text-slate-400 hover:text-slate-200 transition-colors"
                      >
                        {t.adminBindingsCancel}
                      </button>
                    </div>
                  ) : (
                    <span className="font-mono text-xs text-slate-300">
                      {b.model_id ? `${b.provider_id}/${b.model_id}` : t.adminBindingsNoBinding}
                    </span>
                  )}
                  {editing === b.facet_key && saveError && (
                    <AlertaError className="text-xs mt-1">
                      {mensajeDeGuardado(t, saveError)}
                      {codigoDe(saveError) === CODIGO_SIN_CONTRATO && botonDeclarar(saveError.response.data.detail)}
                    </AlertaError>
                  )}
                  {b.ultimo_rechazo && (
                    // Rastro del último 409 del guard para esta faceta
                    // (model_catalog_audit), posterior a su último cambio aprobado.
                    <div className="text-xs text-yellow-400 mt-1">
                      <span>{t.adminBindingsUltimoRechazo(b.ultimo_rechazo.performed_at?.slice(0, 16) || '')}</span>{' '}
                      <span>{textoDeDetalleDeBinding(t, b.ultimo_rechazo) || b.ultimo_rechazo.code}</span>
                      {botonDeclarar(b.ultimo_rechazo)}
                    </div>
                  )}
                </td>
                <td className={`px-4 py-3 text-xs font-semibold ${CAPABILITY_STYLE[b.capability_check]}`}>
                  {t[`adminBindingsCapability${b.capability_check.charAt(0).toUpperCase()}${b.capability_check.slice(1)}`]}
                </td>
                <td className="px-4 py-3">
                  {editing !== b.facet_key && (
                    <button
                      onClick={() => startEdit(b)}
                      className="text-xs px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 text-slate-300 transition-colors"
                    >
                      {t.adminBindingsEdit}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
