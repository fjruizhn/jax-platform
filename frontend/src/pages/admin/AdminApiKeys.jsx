import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import { FACET_COLORS } from '../../store/useJaxStore'

export default function AdminApiKeys() {
  const { t } = useI18n()
  const [providers, setProviders] = useState([])
  const [testing, setTesting] = useState({})
  const [testResult, setTestResult] = useState({})
  const [rotating, setRotating] = useState(null)
  const [newKey, setNewKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [modelsByFacet, setModelsByFacet] = useState({})
  const [managingFacet, setManagingFacet] = useState(null)
  const [managingPos, setManagingPos] = useState({ top: 0, left: 0 })
  const [addingModelFor, setAddingModelFor] = useState(null)
  const [newModel, setNewModel] = useState({ provider_id: '', model_name: '' })
  const [modelError, setModelError] = useState({})
  const [deleteConfirm, setDeleteConfirm] = useState(null)
  const [deleteAnswer, setDeleteAnswer] = useState('')

  useEffect(() => {
    api.get('/admin/keys').then(r => {
      setProviders(r.data.providers)
      r.data.providers.forEach(p => loadModels(p.facet))
    }).catch(() => {})
  }, [])

  function loadModels(facet) {
    api.get(`/admin/facet-models/${facet}`)
      .then(r => setModelsByFacet(m => ({ ...m, [facet]: r.data.models })))
      .catch(() => setModelsByFacet(m => ({ ...m, [facet]: [] })))
  }

  async function handleModelChange(facet, modelId) {
    setModelError(e => ({ ...e, [facet]: null }))
    try {
      await api.put(`/admin/facet-models/${facet}/active/${modelId}`)
      loadModels(facet)
      const { data } = await api.get('/admin/keys')
      setProviders(data.providers)
    } catch {
      setModelError(e => ({ ...e, [facet]: true }))
    }
  }

  async function handleAddModel(facet) {
    if (!newModel.provider_id.trim() || !newModel.model_name.trim()) return
    setSaving(true)
    try {
      await api.post(`/admin/facet-models/${facet}`, newModel)
      setAddingModelFor(null)
      setManagingFacet(null)
      setNewModel({ provider_id: '', model_name: '' })
      loadModels(facet)
    } catch {
    } finally {
      setSaving(false)
    }
  }

  async function handleDeleteModel(facet, modelId) {
    setModelError(e => ({ ...e, [facet]: null }))
    try {
      await api.delete(`/admin/facet-models/${facet}/${modelId}`)
      loadModels(facet)
    } catch {
      setModelError(e => ({ ...e, [facet]: true }))
    }
  }

  function askDeleteConfirmation(facet, model) {
    const a = 1 + Math.floor(Math.random() * 9)
    const b = 1 + Math.floor(Math.random() * 9)
    setDeleteAnswer('')
    setDeleteConfirm({ facet, modelId: model.id, modelName: model.model_name, a, b })
  }

  async function confirmDeleteModel() {
    if (!deleteConfirm) return
    if (parseInt(deleteAnswer, 10) !== deleteConfirm.a + deleteConfirm.b) return
    await handleDeleteModel(deleteConfirm.facet, deleteConfirm.modelId)
    setDeleteConfirm(null)
    setDeleteAnswer('')
  }

  async function handleTest(id) {
    setTesting(p => ({ ...p, [id]: true }))
    setTestResult(p => ({ ...p, [id]: null }))
    try {
      const { data } = await api.post(`/admin/keys/${id}/test`)
      setTestResult(p => ({ ...p, [id]: data }))
    } catch {
      setTestResult(p => ({ ...p, [id]: { ok: false, error: 'Error' } }))
    } finally {
      setTesting(p => ({ ...p, [id]: false }))
    }
  }

  async function handleRotate(id) {
    if (!newKey.trim()) return
    setSaving(true)
    try {
      await api.put(`/admin/keys/${id}`, { api_key: newKey.trim() })
      setRotating(null)
      setNewKey('')
      const { data } = await api.get('/admin/keys')
      setProviders(data.providers)
    } catch {
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <h1 className="text-xl font-bold text-slate-100 mb-6">{t.adminApiKeys}</h1>

      <div className="rounded-lg border border-slate-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 border-b border-slate-800">
            <tr>
              {[t.adminKeyProvider, t.adminKeyFacet, t.adminKeyModel, t.adminKeyValue, t.adminKeyStatus, ''].map(h => (
                <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/50">
            {providers.map(p => {
              const res = testResult[p.id]
              return (
                <tr key={p.id} className="bg-slate-900/50 hover:bg-slate-800/30 transition-colors">
                  <td className="px-4 py-3 font-medium text-slate-200">{p.name}</td>
                  <td className="px-4 py-3">
                    <span className="text-xs font-semibold px-2 py-0.5 rounded" style={{ color: FACET_COLORS[p.facet] || '#94a3b8', backgroundColor: (FACET_COLORS[p.facet] || '#94a3b8') + '20' }}>
                      {p.facet}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {(() => {
                      const models = modelsByFacet[p.facet] || []
                      const active = models.find(m => m.is_active)
                      const selectedId = active ? active.id : ''
                      return (
                        <div className="relative flex items-center gap-1.5">
                          <select
                            value={selectedId}
                            onChange={e => handleModelChange(p.facet, e.target.value)}
                            disabled={!models.length}
                            className="bg-slate-800 border border-slate-600 rounded-lg px-2 py-1 text-xs font-mono text-slate-200 focus:outline-none focus:border-purple-500 disabled:opacity-40"
                          >
                            {!models.length && <option value="">{p.model}</option>}
                            {models.map(m => (
                              <option key={m.id} value={m.id}>{m.model_name}</option>
                            ))}
                          </select>
                          <button
                            onClick={e => {
                              const rect = e.currentTarget.getBoundingClientRect()
                              setManagingPos({ top: rect.bottom + 4, left: rect.left })
                              setManagingFacet(f => f === p.facet ? null : p.facet)
                              setAddingModelFor(null)
                            }}
                            title={t.adminKeyAddModel}
                            className="text-xs w-5 h-5 flex items-center justify-center rounded bg-slate-700 hover:bg-slate-600 text-slate-300 transition-colors"
                          >
                            ⋯
                          </button>
                          {modelError[p.facet] && (
                            <span className="text-xs text-red-400">{t.adminKeyFail}</span>
                          )}

                          {managingFacet === p.facet && (
                            <div
                              style={{ top: managingPos.top, left: managingPos.left }}
                              className="fixed z-20 min-w-[220px] bg-slate-900 border border-slate-700 rounded-lg shadow-2xl py-1"
                            >
                              {models.map(m => (
                                <div key={m.id} className="flex items-center justify-between px-2 py-1 hover:bg-slate-800/60">
                                  <span className={`text-xs font-mono truncate ${m.is_active ? 'text-purple-300 font-semibold' : 'text-slate-300'}`}>
                                    {m.model_name}
                                  </span>
                                  <button
                                    onClick={() => askDeleteConfirmation(p.facet, m)}
                                    disabled={m.is_active}
                                    title={m.is_active ? t.adminKeyModelDeleteActive : t.adminKeyModelDelete}
                                    className="ml-2 text-xs text-slate-500 hover:text-red-400 disabled:opacity-20 disabled:hover:text-slate-500"
                                  >
                                    🗑
                                  </button>
                                </div>
                              ))}
                              {addingModelFor === p.facet ? (
                                <div className="px-2 py-1.5 border-t border-slate-700 mt-1 space-y-1">
                                  <input
                                    type="text"
                                    value={newModel.provider_id}
                                    onChange={e => setNewModel(n => ({ ...n, provider_id: e.target.value }))}
                                    placeholder={t.adminKeyModelProvider}
                                    className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-purple-500"
                                  />
                                  <input
                                    type="text"
                                    value={newModel.model_name}
                                    onChange={e => setNewModel(n => ({ ...n, model_name: e.target.value }))}
                                    placeholder={t.adminKeyModelName}
                                    className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-purple-500"
                                  />
                                  <button
                                    onClick={() => handleAddModel(p.facet)}
                                    disabled={saving || !newModel.provider_id.trim() || !newModel.model_name.trim()}
                                    className="w-full text-xs px-2 py-1 rounded bg-purple-600 hover:bg-purple-700 text-white font-semibold disabled:opacity-50 transition-colors"
                                  >
                                    {t.adminKeyModelAdd}
                                  </button>
                                </div>
                              ) : (
                                <button
                                  onClick={() => { setAddingModelFor(p.facet); setNewModel({ provider_id: '', model_name: '' }) }}
                                  className="w-full text-left px-2 py-1 text-xs text-purple-300 hover:bg-slate-800/60 border-t border-slate-700 mt-1"
                                >
                                  + {t.adminKeyAddModel}
                                </button>
                              )}
                            </div>
                          )}
                        </div>
                      )
                    })()}
                  </td>
                  <td className="px-4 py-3">
                    {p.has_key ? (
                      <span className="font-mono text-xs text-slate-400">••••{p.key_last4}</span>
                    ) : (
                      <span className="text-xs text-red-400">{t.adminKeyMissing}</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {res ? (
                      <span className={`text-xs font-semibold ${res.ok ? 'text-green-400' : 'text-red-400'}`}>
                        {res.ok ? `${t.adminKeyOk} ${res.latency_ms ? t.adminKeyLatency(res.latency_ms) : ''}` : `${t.adminKeyFail}: ${res.error || ''}`}
                      </span>
                    ) : (
                      <span className={`text-xs ${p.has_key ? 'text-green-400' : 'text-slate-500'}`}>
                        {p.has_key ? '●' : '○'} {p.status}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleTest(p.id)}
                        disabled={testing[p.id] || !p.has_key}
                        className="text-xs px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 text-slate-300 disabled:opacity-40 transition-colors"
                      >
                        {testing[p.id] ? t.adminKeyTesting : t.adminKeyTest}
                      </button>
                      <button
                        onClick={() => { setRotating(p.id); setNewKey('') }}
                        className="text-xs px-2 py-1 rounded bg-purple-900/50 hover:bg-purple-800/50 text-purple-300 transition-colors"
                      >
                        {t.adminKeyRotate}
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Modal rotación */}
      {rotating && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 w-full max-w-md shadow-2xl">
            <h2 className="text-sm font-semibold text-slate-200 mb-4">
              {t.adminKeyEnter} {providers.find(p => p.id === rotating)?.name}
            </h2>
            <input
              type="password"
              value={newKey}
              onChange={e => setNewKey(e.target.value)}
              placeholder={t.adminKeyNewValue}
              className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-purple-500 mb-4 font-mono"
            />
            <div className="flex gap-2 justify-end">
              <button onClick={() => setRotating(null)} className="px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-slate-200 transition-colors">{t.adminCreateCancel}</button>
              <button
                onClick={() => handleRotate(rotating)}
                disabled={saving || !newKey.trim()}
                className="px-4 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-700 text-white text-sm font-semibold disabled:opacity-50 transition-colors"
              >
                {saving ? t.attachUploading : t.adminKeySave}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal confirmación aritmética de DELETE */}
      {deleteConfirm && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 w-full max-w-md shadow-2xl">
            <h2 className="text-sm font-semibold text-slate-200 mb-2">
              {t.adminKeyModelDeleteConfirmTitle(deleteConfirm.modelName)}
            </h2>
            <p className="text-xs text-slate-400 mb-3">
              {t.adminKeyModelDeleteConfirmSum(deleteConfirm.a, deleteConfirm.b)}
            </p>
            <input
              type="text"
              inputMode="numeric"
              autoFocus
              value={deleteAnswer}
              onChange={e => setDeleteAnswer(e.target.value)}
              placeholder={t.adminKeyModelDeleteConfirmPlaceholder}
              className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-red-500 mb-4 font-mono"
            />
            <div className="flex gap-2 justify-end">
              <button onClick={() => { setDeleteConfirm(null); setDeleteAnswer('') }} className="px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-slate-200 transition-colors">{t.adminCreateCancel}</button>
              <button
                onClick={confirmDeleteModel}
                disabled={parseInt(deleteAnswer, 10) !== deleteConfirm.a + deleteConfirm.b}
                className="px-4 py-1.5 rounded-lg bg-red-600 hover:bg-red-700 text-white text-sm font-semibold disabled:opacity-50 transition-colors"
              >
                {t.adminKeyModelDeleteConfirmButton}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
