import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import { codigoDe } from '../../api/errores'
import AlertaError from '../../components/AlertaError'
import { useTema } from '../../store/useTema'

// Códigos con los que PUT /admin/config rechaza (backend/api/admin/config_admin.py).
// Cada uno tiene su texto; cualquier otro cae en el genérico, nunca en silencio
// (2026-09-14: el catch estaba vacío y quien guardaba no veía por qué no se guardó).
const CODIGOS_CONOCIDOS = new Set(['config_clave_reservada', 'config_collation_desconocida'])

function claveDeError(err) {
  const codigo = codigoDe(err)
  return CODIGOS_CONOCIDOS.has(codigo) ? codigo : 'adminSettingsSaveError'
}

export default function AdminSettings() {
  const { t } = useI18n()
  const [config, setConfig] = useState({})
  // Sin una carga buena no se guarda: los campos mostrarían valores por
  // defecto y "Guardar" enviaría una lista vacía que el backend acepta.
  const [cargado, setCargado] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  // Clave de i18n del error vigente: se traduce al renderizar.
  const [error, setError] = useState(null)

  useEffect(() => {
    api.get('/admin/config').then(r => {
      const map = {}
      r.data.config.forEach(({ key, value }) => { map[key] = value })
      setConfig(map)
      setCargado(true)
    }).catch(() => setError('adminSettingsLoadError'))
  }, [])

  function set(key, value) {
    setConfig(c => ({ ...c, [key]: value }))
  }

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      const items = Object.entries(config).map(([key, value]) => ({ key, value: String(value) }))
      await api.put('/admin/config', items)
      // El nuevo predeterminado se ve en este navegador sin recargar (spec
      // §5.2.4). Decisión de Fernando (2026-09-14): acá también fija la
      // elección del propio admin, aunque tuviera otra -- los demás usuarios
      // conservan la suya (sincronizarApariencia no cambia).
      useTema.getState().fijarPredeterminadoComoEleccion(config.theme_default)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (err) {
      // Un "Guardado" de un intento anterior no puede convivir con este error.
      setSaved(false)
      setError(claveDeError(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <h1 className="text-xl font-bold text-texto-fuerte mb-6">{t.adminSettingsTitle}</h1>

      <div className="max-w-lg space-y-4">
        <div>
          <label className="block text-xs font-semibold text-texto-suave uppercase tracking-wider mb-1">{t.adminSettingsSystemName}</label>
          <input
            value={config.system_name || ''}
            onChange={e => set('system_name', e.target.value)}
            className="w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco"
          />
        </div>
        <div>
          <label className="block text-xs font-semibold text-texto-suave uppercase tracking-wider mb-1">{t.adminSettingsLang}</label>
          <select value={config.lang_default || 'es'} onChange={e => set('lang_default', e.target.value)} className="w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco">
            <option value="es">Español</option>
            <option value="en">English</option>
          </select>
        </div>
        <div>
          <label className="block text-xs font-semibold text-texto-suave uppercase tracking-wider mb-1">{t.adminSettingsTheme}</label>
          <select value={config.theme_default || 'dark'} onChange={e => set('theme_default', e.target.value)} className="w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco">
            <option value="dark">{t.adminSettingsDark}</option>
            <option value="light">{t.adminSettingsLight}</option>
          </select>
        </div>
        <div>
          <label className="block text-xs font-semibold text-texto-suave uppercase tracking-wider mb-1">{t.adminSettingsTimeout}</label>
          <input type="number" min="5" max="1440" value={config.session_timeout_min || '60'} onChange={e => set('session_timeout_min', e.target.value)} className="w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco" />
        </div>
        <div>
          <label className="block text-xs font-semibold text-texto-suave uppercase tracking-wider mb-1">{t.adminSettingsMaxPipelines}</label>
          <input type="number" min="1" max="5" value={config.max_pipelines || '1'} onChange={e => set('max_pipelines', e.target.value)} className="w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco" />
        </div>
        <div>
          <label className="block text-xs font-semibold text-texto-suave uppercase tracking-wider mb-1">{t.adminSettingsRetention}</label>
          <input type="number" min="1" max="365" value={config.web_task_retention_days || '7'} onChange={e => set('web_task_retention_days', e.target.value)} className="w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco" />
        </div>

        {error && (
          <AlertaError className="text-sm">{t[error] ?? t.adminSettingsSaveError}</AlertaError>
        )}

        <div className="pt-2">
          <button
            onClick={handleSave}
            disabled={saving || !cargado}
            className="px-5 py-2 rounded-lg bg-acento hover:bg-acento-hover text-sobre-color text-sm font-semibold disabled:opacity-50 transition-colors"
          >
            {saved ? `✓ ${t.adminSettingsSaved}` : saving ? t.adminBindingsSaving : t.adminSettingsSave}
          </button>
        </div>
      </div>
    </div>
  )
}
