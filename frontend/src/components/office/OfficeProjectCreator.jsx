import { useRef, useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import Dialogo from '../Dialogo'
import { TAMANO_BOTON_ACCION } from '../../tema/botones'
import { officeProjectsApi } from '../../api/virtualOffice'

const INPUT = 'w-full min-h-11 bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus-visible:ring-2 focus-visible:ring-foco'
const BUTTON = `${TAMANO_BOTON_ACCION} rounded border border-borde px-3 text-texto hover:bg-hundido disabled:opacity-60`
const INTENT_STORAGE_PREFIX = 'axioma.office.project-create.v1'

function storedIntentKey(actorUserId, signature) {
  if (!Number.isInteger(actorUserId) || actorUserId < 1) return null
  return `${INTENT_STORAGE_PREFIX}:${actorUserId}:${encodeURIComponent(signature)}`
}

function validIntent(value, signature) {
  return value && value.signature === signature
    && typeof value.key === 'string'
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value.key)
}

export default function OfficeProjectCreator({ tenantId, actorUserId, onCreated, onForbidden }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const intent = useRef(null)

  function persistedIntent(signature) {
    const key = storedIntentKey(actorUserId, signature)
    if (!key) return null
    try {
      const value = JSON.parse(sessionStorage.getItem(key) || 'null')
      return validIntent(value, signature) ? value : null
    } catch { return null }
  }

  function saveIntent(value) {
    const key = storedIntentKey(actorUserId, value.signature)
    if (!key) return
    try { sessionStorage.setItem(key, JSON.stringify(value)) } catch { /* unavailable storage keeps in-memory retry */ }
  }

  function clearIntent(value) {
    const key = value && storedIntentKey(actorUserId, value.signature)
    if (!key) return
    try { sessionStorage.removeItem(key) } catch { /* unavailable storage needs no cleanup */ }
  }

  function close() {
    if (busy) return
    intent.current = null
    setOpen(false); setName(''); setDescription(''); setError(null)
  }

  async function create(event) {
    event.preventDefault()
    const trimmedName = name.trim()
    if (busy || !trimmedName || !Number.isInteger(tenantId) || tenantId < 1) return
    const payload = { tenant_id: tenantId, name: trimmedName, description: description.trim() || null }
    const signature = JSON.stringify(payload)
    if (intent.current?.signature !== signature) {
      intent.current = persistedIntent(signature) || { signature, key: crypto.randomUUID() }
      saveIntent(intent.current)
    }
    setBusy(true); setError(null)
    try {
      const { data } = await officeProjectsApi.create(payload, intent.current.key)
      if (!data || !Number.isInteger(data.id)) throw new Error('invalid project create response')
      clearIntent(intent.current)
      intent.current = null
      await onCreated?.(data.id)
      setOpen(false); setName(''); setDescription(''); setError(null)
    } catch (err) {
      if (err?.response?.status === 403) { await onForbidden?.(); close(); return }
      setError(err?.response?.status === 409 ? t.virtualOffice.creator.conflict : t.virtualOffice.creator.error)
    } finally { setBusy(false) }
  }

  return <>
    <button type="button" onClick={() => setOpen(true)} className={`${TAMANO_BOTON_ACCION} mt-4 rounded bg-accion px-3 text-sobre-color`}>{t.virtualOffice.creator.open}</button>
    {open && <Dialogo idTitulo="office-project-create" titulo={t.virtualOffice.creator.title} onCerrar={close}>
      <form onSubmit={create} className="space-y-3">
        <p className="text-sm text-texto-suave">{t.virtualOffice.creator.intro}</p>
        <label className="block text-sm text-texto-suave" htmlFor="office-project-name">{t.virtualOffice.creator.name}<input id="office-project-name" required maxLength={255} disabled={busy} value={name} onChange={(event) => { setName(event.target.value); setError(null) }} className={`${INPUT} mt-1`} /></label>
        <label className="block text-sm text-texto-suave" htmlFor="office-project-description">{t.virtualOffice.creator.description}<textarea id="office-project-description" maxLength={2000} disabled={busy} value={description} onChange={(event) => { setDescription(event.target.value); setError(null) }} className={`${INPUT} mt-1 min-h-24`} /></label>
        {error && <p role="alert" className="text-sm text-peligro">{error}</p>}
        <div className="flex flex-wrap justify-end gap-2"><button type="button" disabled={busy} onClick={close} className={BUTTON}>{t.virtualOffice.cancel}</button><button disabled={busy} className={`${TAMANO_BOTON_ACCION} rounded bg-accion px-3 text-sobre-color disabled:opacity-60`}>{busy ? t.virtualOffice.creator.creating : t.virtualOffice.creator.create}</button></div>
      </form>
    </Dialogo>}
  </>
}
