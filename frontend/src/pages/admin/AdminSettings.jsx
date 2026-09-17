import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import { codigoDe } from '../../api/errores'
import AlertaError from '../../components/AlertaError'
import { useTema } from '../../store/useTema'
import { sincronizarApariencia } from '../../apariencia/sincronizarApariencia'

// Códigos con los que PUT /admin/config rechaza (backend/api/admin/config_admin.py).
// Cada uno tiene su texto; cualquier otro cae en el genérico, nunca en silencio
// (2026-09-14: el catch estaba vacío y quien guardaba no veía por qué no se guardó).
const CODIGOS_CONOCIDOS = new Set(['config_clave_reservada', 'config_collation_desconocida'])

// Etiqueta de cada ajuste que el backend valida (frente C, 2026-09-16): el
// error config_valor_invalido nombra el campo, no la clave técnica.
const ETIQUETAS = {
  session_timeout_min: 'adminSettingsTimeout',
  max_pipelines: 'adminSettingsMaxPipelines',
  web_task_retention_days: 'adminSettingsRetention',
  lang_default: 'adminSettingsLang',
  system_name: 'adminSettingsSystemName',
}

const CLASE_ETIQUETA = 'block text-xs font-semibold text-texto-suave uppercase tracking-wider mb-1'
const CLASE_CAMPO = 'w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco'
const CLASE_AYUDA = 'text-xs text-texto-tenue mt-1'

function errorDeGuardado(err) {
  const codigo = codigoDe(err)
  if (codigo === 'config_valor_invalido') return { codigo, clave: err.response.data.detail.clave }
  return { codigo: CODIGOS_CONOCIDOS.has(codigo) ? codigo : 'adminSettingsSaveError' }
}

function textoDeError(t, error) {
  if (error.codigo === 'config_valor_invalido') {
    return t.config_valor_invalido(t[ETIQUETAS[error.clave]] ?? error.clave)
  }
  return t[error.codigo] ?? t.adminSettingsSaveError
}

function CampoNumero({ id, etiqueta, ayuda, valor, limite, onChange }) {
  return (
    <div>
      <label htmlFor={id} className={CLASE_ETIQUETA}>{etiqueta}</label>
      <input id={id} type="number" min={limite?.min} max={limite?.max} value={valor ?? ''}
        onChange={e => onChange(e.target.value)} className={CLASE_CAMPO} />
      {ayuda && <p className={CLASE_AYUDA}>{ayuda}</p>}
    </div>
  )
}

export default function AdminSettings() {
  const { t } = useI18n()
  const [config, setConfig] = useState({})
  // Rangos del SERVIDOR (ajustes.py): la pantalla no los copia.
  const [limites, setLimites] = useState({})
  // Sin una carga buena no se guarda: "Guardar" enviaría una lista vacía que el backend acepta.
  const [cargado, setCargado] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  // Error vigente como { codigo, clave? }: se traduce al renderizar.
  const [error, setError] = useState(null)

  useEffect(() => {
    api.get('/admin/config').then(r => {
      const map = {}
      r.data.config.forEach(({ key, value }) => { map[key] = value })
      setConfig(map)
      setLimites(r.data.limites ?? {})
      setCargado(true)
    }).catch(() => setError({ codigo: 'adminSettingsLoadError' }))
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
      // elección del propio admin, aunque tuviera otra.
      useTema.getState().fijarPredeterminadoComoEleccion(config.theme_default)
      // Nombre e idioma nuevos sin recargar (frente C). Si la lectura falla,
      // quedan los últimos conocidos: el guardado ya se confirmó.
      sincronizarApariencia().catch(() => {})
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (err) {
      // Un "Guardado" de un intento anterior no puede convivir con este error.
      setSaved(false)
      setError(errorDeGuardado(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <h1 className="text-xl font-bold text-texto-fuerte mb-6">{t.adminSettingsTitle}</h1>

      <div className="max-w-lg space-y-4">
        <div>
          <label htmlFor="ajuste-system-name" className={CLASE_ETIQUETA}>{t.adminSettingsSystemName}</label>
          <input
            id="ajuste-system-name"
            value={config.system_name ?? ''}
            maxLength={limites.system_name?.max_largo}
            onChange={e => set('system_name', e.target.value)}
            className={CLASE_CAMPO}
          />
        </div>
        <div>
          <label htmlFor="ajuste-lang-default" className={CLASE_ETIQUETA}>{t.adminSettingsLang}</label>
          <select id="ajuste-lang-default" value={config.lang_default ?? ''} onChange={e => set('lang_default', e.target.value)} className={CLASE_CAMPO}>
            <option value="" disabled hidden />
            <option value="es">Español</option>
            <option value="en">English</option>
          </select>
        </div>
        <div>
          <label htmlFor="ajuste-theme-default" className={CLASE_ETIQUETA}>{t.adminSettingsTheme}</label>
          <select id="ajuste-theme-default" value={config.theme_default || 'dark'} onChange={e => set('theme_default', e.target.value)} className={CLASE_CAMPO}>
            <option value="dark">{t.adminSettingsDark}</option>
            <option value="light">{t.adminSettingsLight}</option>
          </select>
        </div>
        <CampoNumero id="ajuste-session-timeout" etiqueta={t.adminSettingsTimeout} ayuda={t.adminSettingsTimeoutAyuda}
          valor={config.session_timeout_min} limite={limites.session_timeout_min}
          onChange={v => set('session_timeout_min', v)} />
        <CampoNumero id="ajuste-max-pipelines" etiqueta={t.adminSettingsMaxPipelines}
          ayuda={limites.max_pipelines ? t.adminSettingsMaxPipelinesAyuda(limites.max_pipelines.max) : null}
          valor={config.max_pipelines} limite={limites.max_pipelines}
          onChange={v => set('max_pipelines', v)} />
        <CampoNumero id="ajuste-retencion" etiqueta={t.adminSettingsRetention} ayuda={t.adminSettingsRetentionAyuda}
          valor={config.web_task_retention_days} limite={limites.web_task_retention_days}
          onChange={v => set('web_task_retention_days', v)} />

        {error && (
          <AlertaError className="text-sm">{textoDeError(t, error)}</AlertaError>
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
