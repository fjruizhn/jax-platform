import { useState, useEffect, useCallback } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import { FACET_COLORS } from '../../store/useJaxStore'

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

  const loadBindings = useCallback(() => {
    api.get('/admin/facet-bindings').then(r => setBindings(r.data.bindings)).catch(() => {})
  }, [])

  useEffect(() => {
    loadBindings()
    api.get('/admin/models?status=available').then(r => setModels(r.data.models)).catch(() => {})
  }, [loadBindings])

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
      setSaveError(e?.response?.data?.detail || String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <h2 className="text-sm font-semibold text-slate-200 mb-4">{t.adminBindingsTitle}</h2>
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
                    <p className="text-xs text-red-400 mt-1">{t.adminBindingsSaveError(saveError)}</p>
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
