import { useState, useEffect, useCallback } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'

// Espejo de _DISPATCHABLE_TRANSPORTS en backend/api/admin/motors.py -- solo
// para el badge informativo antes de que el backend responda; el backend
// es la fuente real (campo `dispatchable` en cada fila y en el POST).
const _KNOWN_DISPATCHABLE = new Set(['http_openai_compat', 'ollama'])

const EMPTY_FORM = {
  key: '',
  provider_id: '',
  model_id: '',
  transport: 'http_openai_compat',
  max_tokens: '',
  default_timeout_seconds: 600,
  supports_reasoning: false,
  sandbox_only: true,
}

export default function AdminMotors() {
  const { t } = useI18n()
  const [motors, setMotors] = useState([])
  const [transportValues, setTransportValues] = useState([])
  const [dispatchableTransports, setDispatchableTransports] = useState([])
  const [models, setModels] = useState([])
  const [capabilities, setCapabilities] = useState([])
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const [selectedCapabilities, setSelectedCapabilities] = useState({}) // { capability_key: priority }
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)

  const loadMotors = useCallback(() => {
    api.get('/admin/motors').then(r => {
      setMotors(r.data.motors)
      setTransportValues(r.data.transport_values || [])
      setDispatchableTransports(r.data.dispatchable_transports || [])
    }).catch(() => {})
  }, [])

  useEffect(() => {
    loadMotors()
    api.get('/admin/models').then(r => setModels(r.data.models)).catch(() => {})
    api.get('/motors/capabilities').then(r => setCapabilities(r.data.capabilities)).catch(() => {})
  }, [loadMotors])

  function openCreate() {
    setForm(EMPTY_FORM)
    setSelectedCapabilities({})
    setSaveError(null)
    setCreating(true)
  }

  function toggleCapability(key) {
    setSelectedCapabilities(prev => {
      const next = { ...prev }
      if (key in next) {
        delete next[key]
      } else {
        const cap = capabilities.find(c => c.key === key)
        next[key] = cap ? cap.allowed_motors.length : 0
      }
      return next
    })
  }

  function setCapabilityPriority(key, priority) {
    setSelectedCapabilities(prev => ({ ...prev, [key]: priority }))
  }

  const providerIds = [...new Set(models.map(m => m.provider_id))].sort()
  const modelsForProvider = models.filter(m => m.provider_id === form.provider_id)

  async function submitCreate() {
    if (!form.key.trim() || !form.provider_id || !form.model_id || !form.transport) return
    setSaving(true)
    setSaveError(null)
    try {
      await api.post('/admin/motors', {
        key: form.key.trim(),
        provider_id: form.provider_id,
        model_id: form.model_id,
        transport: form.transport,
        max_tokens: form.max_tokens === '' ? 0 : Number(form.max_tokens),
        default_timeout_seconds: Number(form.default_timeout_seconds) || 600,
        supports_reasoning: form.supports_reasoning,
        sandbox_only: form.sandbox_only,
        capabilities: Object.entries(selectedCapabilities).map(([capability_key, priority]) => ({
          capability_key,
          priority: Number(priority) || 0,
        })),
      })
      setCreating(false)
      loadMotors()
      api.get('/motors/capabilities').then(r => setCapabilities(r.data.capabilities)).catch(() => {})
    } catch (e) {
      setSaveError(e?.response?.data?.detail || String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-slate-200">{t.adminMotorsTitle}</h2>
        <button
          onClick={openCreate}
          className="text-xs px-3 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-700 text-white font-semibold transition-colors"
        >
          {t.adminMotorsCreate}
        </button>
      </div>

      <p className="text-xs text-slate-500 mb-4">{t.adminMotorsLimitationNote}</p>

      <div className="rounded-lg border border-slate-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 border-b border-slate-800">
            <tr>
              {[t.adminMotorsKey, t.adminModelsProvider, t.adminModelsModelId, t.adminBindingsTransport,
                t.adminMotorsDispatchable, t.adminMotorsCapabilities, t.adminModelsStatus].map(h => (
                <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/50">
            {motors.map(m => (
              <tr key={m.key} className="bg-slate-900/50 hover:bg-slate-800/30 transition-colors">
                <td className="px-4 py-3 font-medium text-slate-200 font-mono text-xs">{m.key}</td>
                <td className="px-4 py-3 text-xs text-slate-300">{m.provider_id}</td>
                <td className="px-4 py-3 font-mono text-xs text-slate-300">{m.model_id}</td>
                <td className="px-4 py-3 text-xs text-slate-400 font-mono">{m.transport}</td>
                <td className="px-4 py-3 text-xs">
                  {m.dispatchable
                    ? <span className="text-green-400">{t.adminMotorsDispatchableYes}</span>
                    : <span className="text-yellow-400">{t.adminMotorsDispatchableNo}</span>}
                </td>
                <td className="px-4 py-3 text-xs text-slate-400">
                  {m.capabilities.length > 0
                    ? m.capabilities.map(c => c.capability_key).join(', ')
                    : <span className="text-slate-600">{t.adminMotorsNoCapabilities}</span>}
                </td>
                <td className="px-4 py-3 text-xs text-slate-400">{m.status}</td>
              </tr>
            ))}
            {motors.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-6 text-center text-xs text-slate-500">{t.adminMotorsEmpty}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {creating && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 w-full max-w-lg shadow-2xl max-h-[90vh] overflow-y-auto">
            <h2 className="text-sm font-semibold text-slate-200 mb-4">{t.adminMotorsCreateTitle}</h2>

            <label className="block text-xs text-slate-400 mb-1">{t.adminMotorsKey}</label>
            <input
              type="text"
              value={form.key}
              onChange={e => setForm(f => ({ ...f, key: e.target.value }))}
              placeholder={t.adminMotorsKeyPlaceholder}
              className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-purple-500 mb-3 font-mono"
            />

            <label className="block text-xs text-slate-400 mb-1">{t.adminModelsProvider}</label>
            <select
              value={form.provider_id}
              onChange={e => setForm(f => ({ ...f, provider_id: e.target.value, model_id: '' }))}
              className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 mb-3"
            >
              <option value="">{t.adminMotorsSelectProvider}</option>
              {providerIds.map(p => <option key={p} value={p}>{p}</option>)}
            </select>

            <label className="block text-xs text-slate-400 mb-1">{t.adminModelsModelId}</label>
            <select
              value={form.model_id}
              onChange={e => setForm(f => ({ ...f, model_id: e.target.value }))}
              disabled={!form.provider_id}
              className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 mb-3 disabled:opacity-50"
            >
              <option value="">{t.adminMotorsSelectModel}</option>
              {modelsForProvider.map(m => <option key={m.id} value={m.model_id}>{m.model_id}</option>)}
            </select>

            <label className="block text-xs text-slate-400 mb-1">{t.adminBindingsTransport}</label>
            <select
              value={form.transport}
              onChange={e => setForm(f => ({ ...f, transport: e.target.value }))}
              className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500 mb-1"
            >
              {(transportValues.length ? transportValues : ['http_openai_compat', 'ollama']).map(tr => (
                <option key={tr} value={tr}>
                  {tr}{(dispatchableTransports.length ? !dispatchableTransports.includes(tr) : !_KNOWN_DISPATCHABLE.has(tr)) ? ` — ${t.adminMotorsDispatchableNo}` : ''}
                </option>
              ))}
            </select>
            <p className="text-[10px] text-slate-600 mb-3">{t.adminMotorsLimitationNote}</p>

            <div className="grid grid-cols-2 gap-3 mb-3">
              <div>
                <label className="block text-xs text-slate-400 mb-1">{t.adminMotorsMaxTokens}</label>
                <input
                  type="number"
                  min="0"
                  value={form.max_tokens}
                  onChange={e => setForm(f => ({ ...f, max_tokens: e.target.value }))}
                  placeholder="0"
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-400 mb-1">{t.adminMotorsTimeout}</label>
                <input
                  type="number"
                  min="1"
                  value={form.default_timeout_seconds}
                  onChange={e => setForm(f => ({ ...f, default_timeout_seconds: e.target.value }))}
                  className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500"
                />
              </div>
            </div>

            <label className="flex items-center gap-2 text-xs text-slate-400 mb-1">
              <input
                type="checkbox"
                checked={form.supports_reasoning}
                onChange={e => setForm(f => ({ ...f, supports_reasoning: e.target.checked }))}
              />
              {t.adminMotorsSupportsReasoning}
            </label>
            <label className="flex items-center gap-2 text-xs text-slate-400 mb-4">
              <input
                type="checkbox"
                checked={form.sandbox_only}
                onChange={e => setForm(f => ({ ...f, sandbox_only: e.target.checked }))}
              />
              {t.adminMotorsSandboxOnly}
            </label>

            <label className="block text-xs text-slate-400 mb-2">{t.adminMotorsCapabilities}</label>
            <div className="max-h-40 overflow-y-auto border border-slate-800 rounded-lg p-2 mb-4">
              {capabilities.map(cap => (
                <div key={cap.key} className="flex items-center gap-2 py-1">
                  <input
                    type="checkbox"
                    checked={cap.key in selectedCapabilities}
                    onChange={() => toggleCapability(cap.key)}
                  />
                  <span className="text-xs text-slate-300 font-mono flex-1">{cap.key}</span>
                  {cap.key in selectedCapabilities && (
                    <input
                      type="number"
                      min="0"
                      value={selectedCapabilities[cap.key]}
                      onChange={e => setCapabilityPriority(cap.key, e.target.value)}
                      title={t.adminMotorsPriority}
                      className="w-16 bg-slate-800 border border-slate-600 rounded px-1.5 py-0.5 text-xs text-slate-200 focus:outline-none focus:border-purple-500"
                    />
                  )}
                </div>
              ))}
            </div>

            {saveError && <p className="text-xs text-red-400 mb-3">{t.adminMotorsSaveError(saveError)}</p>}

            <div className="flex gap-2 justify-end">
              <button onClick={() => setCreating(false)} className="px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-slate-200 transition-colors">
                {t.adminCreateCancel}
              </button>
              <button
                onClick={submitCreate}
                disabled={saving || !form.key.trim() || !form.provider_id || !form.model_id}
                className="px-4 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-700 text-white text-sm font-semibold disabled:opacity-50 transition-colors"
              >
                {saving ? t.adminBindingsSaving : t.adminMotorsSave}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
