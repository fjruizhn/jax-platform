import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import { useJaxStore } from '../../store/useJaxStore'
import { TAMANO_BOTON_ACCION } from '../../tema/botones'
import AdminModelCatalog from './AdminModelCatalog'
import AdminFacetBindings from './AdminFacetBindings'
import AdminMotors from './AdminMotors'
import PasswordInput from '../../components/PasswordInput'
import Dialogo from '../../components/Dialogo'
import ConfirmacionSuma from '../../components/ConfirmacionSuma'

const TABS = [
  { key: 'providers', labelKey: 'adminTabProviders' },
  { key: 'models', labelKey: 'adminTabModels' },
  { key: 'bindings', labelKey: 'adminTabBindings' },
  { key: 'motors', labelKey: 'adminTabMotors' },
]

export default function AdminFacetsModels() {
  const { t } = useI18n()
  const addToast = useJaxStore((s) => s.addToast)
  // Pestaña inicial por query param (2026-09-21, pedido de Fernando): el
  // contador de propuestas de BarraUsuario lleva a /admin/keys?tab=models --
  // "de una sola vista" quiere decir aterrizar ahí, no obligar a un clic más.
  // Sólo se lee al montar (useState, no useEffect): cambiar de pestaña a mano
  // sigue siendo el mismo estado local de siempre, sin que la URL lo pise
  // después. `tab` inválido o ausente cae al default de hoy, "providers".
  const [searchParams] = useSearchParams()
  const [activeTab, setActiveTab] = useState(() => {
    const tab = searchParams.get('tab')
    return TABS.some((candidata) => candidata.key === tab) ? tab : 'providers'
  })
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
      setTestResult(p => ({ ...p, [id]: { ok: false, error: t.adminKeyTestError } }))
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
      addToast({ type: 'error', message: t.adminKeyRotateError })
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
      addToast({ type: 'error', message: t.adminKeyRevokeError })
    } finally {
      setRevoking(null)
    }
  }

  return (
    <div>
      <h1 className="text-xl font-bold text-texto-fuerte mb-6">{t.adminFacetsModels}</h1>

      <div className="flex gap-1 border-b border-borde mb-6">
        {TABS.map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              activeTab === tab.key
                ? 'border-acento text-acento-texto'
                : 'border-transparent text-texto-suave hover:text-texto'
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
      <div className="rounded-lg border border-borde overflow-hidden">
        {/* Faceta/modelo NO viven acá — Bloque D los movió a la pestaña
            "Facetas y Bindings" (facet_binding, fuente real). Esta tabla
            era antes la UI de facet_models (legacy): mostraba y editaba un
            modelo "activo" que Bloque C ya había dejado de leer para
            invocar — dos pestañas podían afirmar cosas distintas del mismo
            hecho. Esta pestaña es solo identidad de proveedor + credencial. */}
        <table className="w-full text-sm">
          <thead className="bg-hundido border-b border-borde">
            <tr>
              {[t.adminKeyProvider, t.adminKeyValue, t.adminKeyStatus, ''].map(h => (
                <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-texto-suave uppercase tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-borde/50">
            {providers.map(p => {
              const res = testResult[p.id]
              return (
                <tr key={p.id} className="bg-hundido hover:bg-superficie transition-colors">
                  <td className="px-4 py-3 font-medium text-texto">{p.name}</td>
                  <td className="px-4 py-3">
                    {p.has_key ? (
                      <span className="font-mono text-xs text-texto-suave">••••{p.key_last4}</span>
                    ) : (
                      <span className="text-xs text-peligro">{t.adminKeyMissing}</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {(() => {
                      const cred = credentialsById[p.id]
                      const activeCreds = (cred?.credentials || []).filter(c => c.state === 'active')
                      const mostRecent = activeCreds[0]
                      if (res) {
                        return (
                          <span className={`text-xs font-semibold ${res.ok ? 'text-exito' : 'text-peligro'}`}>
                            {res.ok ? `${t.adminKeyOk} ${res.latency_ms ? t.adminKeyLatency(res.latency_ms) : ''}` : `${t.adminKeyFail}: ${res.error || ''}`}
                          </span>
                        )
                      }
                      if (!activeCreds.length) {
                        return <span className="text-xs text-texto-tenue">○ {t.adminKeyNoActive}</span>
                      }
                      return (
                        <div className="flex flex-col gap-0.5">
                          <span className={`text-xs ${mostRecent.last_health_status === 'ok' ? 'text-exito' : mostRecent.last_health_status === 'failed' ? 'text-peligro' : 'text-texto-suave'}`}>
                            ● {t.adminKeyActiveCount(activeCreds.length)}
                            {mostRecent.last_health_status !== 'unknown' && ` · ${mostRecent.last_health_status}`}
                          </span>
                          {mostRecent.last_verified_at && (
                            <span className="text-[10px] text-texto-tenue">{t.adminKeyLastVerified}: {mostRecent.last_verified_at}</span>
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
                        className={`${TAMANO_BOTON_ACCION} rounded bg-superficie-2 text-texto hover:text-texto-fuerte disabled:opacity-40 transition-colors`}
                      >
                        {testing[p.id] ? t.adminKeyTesting : t.adminKeyTest}
                      </button>
                      <button
                        onClick={() => { setRotating(p.id); setNewKey('') }}
                        className={`${TAMANO_BOTON_ACCION} rounded bg-acento-fondo text-acento-texto border border-transparent hover:border-acento transition-colors`}
                      >
                        {t.adminKeyRotate}
                      </button>
                      <button
                        onClick={() => setRevokeConfirm(p.id)}
                        disabled={revoking === p.id || !(credentialsById[p.id]?.credentials || []).some(c => c.state === 'active')}
                        className={`${TAMANO_BOTON_ACCION} rounded bg-peligro-fondo text-peligro border border-transparent hover:border-peligro-borde disabled:opacity-30 transition-colors`}
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
        <Dialogo idTitulo="llave-rotar-titulo" titulo={`${t.adminKeyEnter} ${providers.find(p => p.id === rotating)?.name ?? ''}`}
          onCerrar={() => setRotating(null)}>
          <PasswordInput
            value={newKey}
            onChange={e => setNewKey(e.target.value)}
            placeholder={t.adminKeyNewValue}
            wrapperClassName="mb-4"
            className="w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto placeholder-texto-tenue focus:outline-none focus:border-foco font-mono"
          />
          <div className="flex gap-2 justify-end">
            <button onClick={() => setRotating(null)} className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors">{t.adminCreateCancel}</button>
            <button
              onClick={() => handleRotate(rotating)}
              disabled={saving || !newKey.trim()}
              className="px-4 py-1.5 rounded-lg bg-acento hover:bg-acento-hover text-sobre-color text-sm font-semibold disabled:opacity-50 transition-colors"
            >
              {saving ? t.adminBindingsSaving : t.adminKeySave}
            </button>
          </div>
        </Dialogo>
      )}

      {/* Revocación: corte inmediato, sin gracia -- destructivo: ConfirmacionSuma */}
      {revokeConfirm && (
        <ConfirmacionSuma
          titulo={t.adminKeyRevokeConfirmTitle}
          mensaje={t.adminKeyRevokeConfirmBody}
          textoConfirmar={t.adminKeyRevoke}
          onConfirmar={() => handleRevoke(revokeConfirm)}
          onCancelar={() => setRevokeConfirm(null)}
        />
      )}
    </div>
  )
}
