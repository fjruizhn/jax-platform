import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'

const FACET_COLORS = {
  thot: '#f59e0b', jekyll: '#6366f1', hipatia: '#10b981',
  kimi: '#06b6d4', ada: '#7c3aed',
}

export default function AdminApiKeys() {
  const { t } = useI18n()
  const [providers, setProviders] = useState([])
  const [testing, setTesting] = useState({})
  const [testResult, setTestResult] = useState({})
  const [rotating, setRotating] = useState(null)
  const [newKey, setNewKey] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.get('/admin/keys').then(r => setProviders(r.data.providers)).catch(() => {})
  }, [])

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
                  <td className="px-4 py-3 text-slate-400 text-xs font-mono">{p.model}</td>
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
    </div>
  )
}
