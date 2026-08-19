import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import AdminModelCatalog from './AdminModelCatalog'
import AdminFacetBindings from './AdminFacetBindings'
import AdminMotors from './AdminMotors'

const TABS = [
  { key: 'providers', labelKey: 'adminTabProviders' },
  { key: 'models', labelKey: 'adminTabModels' },
  { key: 'bindings', labelKey: 'adminTabBindings' },
  { key: 'motors', labelKey: 'adminTabMotors' },
]

export default function AdminFacetsModels() {
  const { t } = useI18n()
  const [activeTab, setActiveTab] = useState('providers')
  const [providers, setProviders] = useState([])
  const [credentialsById, setCredentialsById] = useState({})
  const [testing, setTesting] = useState({})
  const [testResult, setTestResult] = useState({})
  const [rotating, setRotating] = useState(null)
  const [revoking, setRevoking] = useState(null)
  const [revokeConfirm, setRevokeConfirm] = useState(null)
  const [newKey, setNewKey] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.get('/admin/keys').then(r => setProviders(r.data.providers)).catch(() => {})
    loadCredentials()
  }, [])

  function loadCredentials() {
    api.get('/admin/credentials').then(r => {
      const byId = {}
      r.data.providers.forEach(p => { byId[p.id] = p })
      setCredentialsById(byId)
    }).catch(() => {})
  }

  async function handleTest(id) {
    setTesting(p => ({ ...p, [id]: true }))
    setTestResult(p => ({ ...p, [id]: null }))
    try {
      const { data } = await api.post(`/admin/credentials/${id}/test`)
      setTestResult(p => ({ ...p, [id]: data }))
      loadCredentials()  // salud persistida — refleja lo que quedó en DB
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
      // Rotar = agregar una credencial nueva activa, sin tocar la anterior
      // (solapamiento con gracia) — antes esto sobreescribía sin aviso.
      await api.post(`/admin/credentials/${id}/rotate`, { api_key: newKey.trim() })
      setRotating(null)
      setNewKey('')
      const { data } = await api.get('/admin/keys')
      setProviders(data.providers)
      loadCredentials()
    } catch {
    } finally {
      setSaving(false)
    }
  }

  async function handleRevoke(id) {
    setRevoking(id)
    try {
      // Revocar = corte inmediato de TODAS las credenciales activas, sin
      // gracia — acción separada de rotar, antes un solo botón hacía mal
      // las dos cosas a la vez.
      await api.post(`/admin/credentials/${id}/revoke`)
      setRevokeConfirm(null)
      loadCredentials()
    } catch {
    } finally {
      setRevoking(null)
    }
  }

  return (
    <div>
      <h1 className="text-xl font-bold text-slate-100 mb-6">{t.adminFacetsModels}</h1>

      <div className="flex gap-1 border-b border-slate-800 mb-6">
        {TABS.map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              activeTab === tab.key
                ? 'border-purple-500 text-purple-300'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            {t[tab.labelKey]}
          </button>
        ))}
      </div>

      {activeTab === 'models' && <AdminModelCatalog />}
      {activeTab === 'bindings' && <AdminFacetBindings />}
      {activeTab === 'motors' && <AdminMotors />}

      {activeTab === 'providers' && (
      <div className="rounded-lg border border-slate-800 overflow-hidden">
        {/* Faceta/modelo NO viven acá — Bloque D los movió a la pestaña
            "Facetas y Bindings" (facet_binding, fuente real). Esta tabla
            era antes la UI de facet_models (legacy): mostraba y editaba un
            modelo "activo" que Bloque C ya había dejado de leer para
            invocar — dos pestañas podían afirmar cosas distintas del mismo
            hecho. Esta pestaña es solo identidad de proveedor + credencial. */}
        <table className="w-full text-sm">
          <thead className="bg-slate-900 border-b border-slate-800">
            <tr>
              {[t.adminKeyProvider, t.adminKeyValue, t.adminKeyStatus, ''].map(h => (
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
                    {p.has_key ? (
                      <span className="font-mono text-xs text-slate-400">••••{p.key_last4}</span>
                    ) : (
                      <span className="text-xs text-red-400">{t.adminKeyMissing}</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {(() => {
                      const cred = credentialsById[p.id]
                      const activeCreds = (cred?.credentials || []).filter(c => c.state === 'active')
                      const mostRecent = activeCreds[0]
                      if (res) {
                        return (
                          <span className={`text-xs font-semibold ${res.ok ? 'text-green-400' : 'text-red-400'}`}>
                            {res.ok ? `${t.adminKeyOk} ${res.latency_ms ? t.adminKeyLatency(res.latency_ms) : ''}` : `${t.adminKeyFail}: ${res.error || ''}`}
                          </span>
                        )
                      }
                      if (!activeCreds.length) {
                        return <span className="text-xs text-slate-500">○ {t.adminKeyNoActive}</span>
                      }
                      return (
                        <div className="flex flex-col gap-0.5">
                          <span className={`text-xs ${mostRecent.last_health_status === 'ok' ? 'text-green-400' : mostRecent.last_health_status === 'failed' ? 'text-red-400' : 'text-slate-400'}`}>
                            ● {t.adminKeyActiveCount(activeCreds.length)}
                            {mostRecent.last_health_status !== 'unknown' && ` · ${mostRecent.last_health_status}`}
                          </span>
                          {mostRecent.last_verified_at && (
                            <span className="text-[10px] text-slate-600">{t.adminKeyLastVerified}: {mostRecent.last_verified_at}</span>
                          )}
                        </div>
                      )
                    })()}
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
                      <button
                        onClick={() => setRevokeConfirm(p.id)}
                        disabled={revoking === p.id || !(credentialsById[p.id]?.credentials || []).some(c => c.state === 'active')}
                        className="text-xs px-2 py-1 rounded bg-red-900/40 hover:bg-red-800/50 text-red-300 disabled:opacity-30 transition-colors"
                      >
                        {revoking === p.id ? t.adminKeyRevoking : t.adminKeyRevoke}
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

      {/* Modal confirmación de revocación — corte inmediato, sin gracia */}
      {revokeConfirm && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 w-full max-w-md shadow-2xl">
            <h2 className="text-sm font-semibold text-red-300 mb-2">{t.adminKeyRevokeConfirmTitle}</h2>
            <p className="text-xs text-slate-400 mb-4">{t.adminKeyRevokeConfirmBody}</p>
            <div className="flex gap-2 justify-end">
              <button onClick={() => setRevokeConfirm(null)} className="px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-slate-200 transition-colors">{t.adminCreateCancel}</button>
              <button
                onClick={() => handleRevoke(revokeConfirm)}
                disabled={revoking === revokeConfirm}
                className="px-4 py-1.5 rounded-lg bg-red-600 hover:bg-red-700 text-white text-sm font-semibold disabled:opacity-50 transition-colors"
              >
                {t.adminKeyRevoke}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
