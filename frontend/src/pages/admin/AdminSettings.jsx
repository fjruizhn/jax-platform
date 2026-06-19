import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'

export default function AdminSettings() {
  const { t } = useI18n()
  const [config, setConfig] = useState({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    api.get('/admin/config').then(r => {
      const map = {}
      r.data.config.forEach(({ key, value }) => { map[key] = value })
      setConfig(map)
    }).catch(() => {})
  }, [])

  function set(key, value) {
    setConfig(c => ({ ...c, [key]: value }))
  }

  async function handleSave() {
    setSaving(true)
    try {
      const items = Object.entries(config).map(([key, value]) => ({ key, value: String(value) }))
      await api.put('/admin/config', items)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch {
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <h1 className="text-xl font-bold text-slate-100 mb-6">{t.adminSettingsTitle}</h1>

      <div className="max-w-lg space-y-4">
        <div>
          <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">{t.adminSettingsSystemName}</label>
          <input
            value={config.system_name || ''}
            onChange={e => set('system_name', e.target.value)}
            className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500"
          />
        </div>
        <div>
          <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">{t.adminSettingsLang}</label>
          <select value={config.lang_default || 'es'} onChange={e => set('lang_default', e.target.value)} className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500">
            <option value="es">Español</option>
            <option value="en">English</option>
          </select>
        </div>
        <div>
          <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">{t.adminSettingsTheme}</label>
          <select value={config.theme_default || 'dark'} onChange={e => set('theme_default', e.target.value)} className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500">
            <option value="dark">{t.adminSettingsDark}</option>
            <option value="light">{t.adminSettingsLight}</option>
          </select>
        </div>
        <div>
          <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">{t.adminSettingsTimeout}</label>
          <input type="number" min="5" max="1440" value={config.session_timeout_min || '60'} onChange={e => set('session_timeout_min', e.target.value)} className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500" />
        </div>
        <div>
          <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">{t.adminSettingsMaxPipelines}</label>
          <input type="number" min="1" max="5" value={config.max_pipelines || '1'} onChange={e => set('max_pipelines', e.target.value)} className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500" />
        </div>
        <div>
          <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">{t.adminSettingsRetention}</label>
          <input type="number" min="1" max="365" value={config.web_task_retention_days || '7'} onChange={e => set('web_task_retention_days', e.target.value)} className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500" />
        </div>

        <div className="pt-2">
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-5 py-2 rounded-lg bg-purple-600 hover:bg-purple-700 text-white text-sm font-semibold disabled:opacity-50 transition-colors"
          >
            {saved ? `✓ ${t.adminSettingsSaved}` : saving ? t.attachUploading : t.adminSettingsSave}
          </button>
        </div>
      </div>
    </div>
  )
}
