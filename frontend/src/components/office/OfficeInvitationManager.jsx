import { useEffect, useRef, useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import { officeInvitationsApi } from '../../api/virtualOffice'
import Dialogo from '../Dialogo'
import { TAMANO_BOTON_ACCION } from '../../tema/botones'

const PAGE_SIZE = 25
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const DELIVERY_STATES = new Set(['QUEUED', 'CLAIMED', 'SMTP_ACCEPTED', 'UNKNOWN', 'CANCELLED'])
const INPUT = 'w-full min-h-11 bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus-visible:ring-2 focus-visible:ring-foco'
const BUTTON = `${TAMANO_BOTON_ACCION} rounded border border-borde px-3 text-texto hover:bg-hundido disabled:opacity-60`

function validInvitation(invitation) {
  return invitation && typeof invitation.id === 'string' && UUID.test(invitation.id) && typeof invitation.email === 'string' && typeof invitation.audience === 'string' && invitation.status === 'ISSUED' && Number.isInteger(invitation.version) && typeof invitation.expires_at === 'string'
    && (invitation.delivery_state === undefined || DELIVERY_STATES.has(invitation.delivery_state))
    && (invitation.delivery_result_code === undefined || invitation.delivery_result_code === null || typeof invitation.delivery_result_code === 'string')
}

function validList(data) {
  return data && Array.isArray(data.invitations) && data.invitations.every(validInvitation) && (data.next_cursor === null || typeof data.next_cursor === 'string')
}

function mutationMessage(status, t) {
  if (status === 409) return t.officeInvitation.conflict
  if (status === 503) return t.officeInvitation.unavailable
  return t.officeInvitation.managerError
}

// This is mounted solely from the server-computed project capability. It never
// infers permission from a browser role, and cursor tokens remain opaque.
export default function OfficeInvitationManager({ projectId, onForbidden }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [email, setEmail] = useState('')
  const [audience, setAudience] = useState('INTERNAL')
  const [invitations, setInvitations] = useState(null)
  const [nextCursor, setNextCursor] = useState(null)
  const [listError, setListError] = useState(false)
  const [notice, setNotice] = useState(null)
  const [revoke, setRevoke] = useState(null)
  const [busy, setBusy] = useState(false)
  const listGeneration = useRef(0)
  const listLoading = useRef(false)
  const issueIntent = useRef(null)
  const revokeKey = useRef(null)
  const previousProjectId = useRef(projectId)

  async function handleForbidden() {
    setOpen(false)
    setRevoke(null)
    await onForbidden?.()
  }

  async function load(cursor = null, append = false) {
    if (listLoading.current) return false
    listLoading.current = true
    const generation = ++listGeneration.current
    setListError(false)
    if (!append) {
      setInvitations(null)
      setNextCursor(null)
    }
    try {
      const { data } = await officeInvitationsApi.list(projectId, { limit: PAGE_SIZE, cursor })
      if (generation !== listGeneration.current) return false
      if (!validList(data)) throw new Error('invalid invitation list response')
      setInvitations((current) => append ? [...(current || []), ...data.invitations] : data.invitations)
      setNextCursor(data.next_cursor)
      return true
    } catch (err) {
      if (generation !== listGeneration.current) return false
      if (err?.response?.status === 403) {
        await handleForbidden()
        return false
      }
      setListError(true)
      return false
    } finally {
      if (generation === listGeneration.current) listLoading.current = false
    }
  }

  useEffect(() => {
    if (previousProjectId.current !== projectId) {
      issueIntent.current = null
      revokeKey.current = null
      setOpen(false)
      setEmail('')
      setAudience('INTERNAL')
      setRevoke(null)
      setNotice(null)
      previousProjectId.current = projectId
    }
    load()
    return () => { listGeneration.current += 1; listLoading.current = false }
  }, [projectId])

  async function refreshAfterMutation() {
    const refreshed = await load()
    if (!refreshed) setNotice(t.officeInvitation.refreshError)
  }

  async function issue(event) {
    event.preventDefault()
    if (busy || !email.trim()) return
    const payload = { email: email.trim(), audience }
    const signature = JSON.stringify(payload)
    if (issueIntent.current?.signature !== signature) issueIntent.current = { signature, key: crypto.randomUUID() }
    setBusy(true)
    setNotice(null)
    try {
      await officeInvitationsApi.issue(projectId, payload, issueIntent.current.key)
      issueIntent.current = null
      setEmail('')
      await refreshAfterMutation()
    } catch (err) {
      if (err?.response?.status === 403) await handleForbidden()
      else {
        setNotice(mutationMessage(err?.response?.status, t))
        if (err?.response?.status === 409 || err?.response?.status === 503) await refreshAfterMutation()
      }
    } finally { setBusy(false) }
  }

  async function confirmRevoke() {
    if (!revoke || busy) return
    revokeKey.current ??= crypto.randomUUID()
    setBusy(true)
    setNotice(null)
    try {
      await officeInvitationsApi.revoke(projectId, revoke.id, revoke.version, revokeKey.current)
      const reissue = revoke.delivery_state === 'UNKNOWN'
      const revoked = revoke
      revokeKey.current = null
      setRevoke(null)
      if (reissue) {
        // UNKNOWN means SMTP outcome was not proven. Revoke this bearer before
        // preparing a distinct, explicitly submitted invitation.
        issueIntent.current = null
        setEmail(revoked.email)
        setAudience(revoked.audience)
        setNotice(t.officeInvitation.reissueReady)
      }
      await refreshAfterMutation()
    } catch (err) {
      if (err?.response?.status === 403) await handleForbidden()
      else {
        setNotice(mutationMessage(err?.response?.status, t))
        if (err?.response?.status === 409) setRevoke(null)
        if (err?.response?.status === 409 || err?.response?.status === 503) await refreshAfterMutation()
      }
    } finally { setBusy(false) }
  }

  function closeManager() {
    if (busy || revoke) return
    issueIntent.current = null
    setEmail('')
    setAudience('INTERNAL')
    setNotice(null)
    setOpen(false)
  }

  return <>
    <button type="button" onClick={() => setOpen(true)} className={`${BUTTON} mt-5`}>{t.officeInvitation.manage}</button>
    {open && <Dialogo idTitulo="office-invitations" titulo={t.officeInvitation.manage} onCerrar={closeManager} className="max-w-lg max-h-[calc(100dvh-2rem)] flex flex-col">
      <div data-office-invitation-content={true} inert={revoke || undefined} aria-hidden={revoke ? 'true' : undefined} className="min-h-0 overflow-y-auto pr-1">
        <form onSubmit={issue} className="space-y-3">
          <label className="block text-sm text-texto-suave" htmlFor="office-invitation-email">{t.officeInvitation.email}<input id="office-invitation-email" type="email" value={email} disabled={busy} onChange={(event) => { setEmail(event.target.value); setNotice(null) }} autoComplete="email" required className={`${INPUT} mt-1`} /></label>
          <label className="block text-sm text-texto-suave" htmlFor="office-invitation-audience">{t.officeInvitation.audience}<select id="office-invitation-audience" value={audience} disabled={busy} onChange={(event) => { setAudience(event.target.value); setNotice(null) }} className={`${INPUT} mt-1`}><option value="INTERNAL">{t.officeInvitation.audiences.INTERNAL}</option><option value="CLIENT_PUBLISHED">{t.officeInvitation.audiences.CLIENT_PUBLISHED}</option></select></label>
          {notice && <p role="alert" className="text-sm text-peligro">{notice}</p>}
          <button disabled={busy} className={`${TAMANO_BOTON_ACCION} w-full rounded bg-accion text-sobre-color disabled:opacity-60`}>{t.officeInvitation.send}</button>
        </form>
        {invitations === null && !listError && <p role="status" className="mt-5 text-sm text-texto-suave">{t.officeInvitation.loading}</p>}
        {listError && <div className="mt-5 space-y-3"><p role="alert" className="text-sm text-peligro">{t.officeInvitation.listError}</p><button type="button" disabled={busy} onClick={() => load()} className={BUTTON}>{t.virtualOffice.projects.more}</button></div>}
        {invitations?.length === 0 && <p role="status" className="mt-5 text-sm text-texto-suave">{t.officeInvitation.empty}</p>}
        {invitations?.length > 0 && <ul className="mt-5 divide-y divide-borde border-y border-borde">{invitations.map((invitation) => <li key={invitation.id} className="flex items-start justify-between gap-3 py-3"><div className="min-w-0 text-sm"><p className="break-all text-texto">{invitation.email}</p><p className="mt-1 text-texto-suave">{t.officeInvitation.status}: {invitation.status}</p>{invitation.delivery_state && <p className="text-texto-suave">{t.officeInvitation.delivery}: {t.officeInvitation.deliveryStates[invitation.delivery_state]}</p>}{invitation.delivery_state === 'UNKNOWN' && <p className="mt-1 text-texto-suave">{t.officeInvitation.deliveryUnknown}</p>}<p className="text-texto-suave">{t.officeInvitation.expiresAt}: {invitation.expires_at}</p></div><button type="button" disabled={busy} onClick={() => { revokeKey.current = null; setRevoke(invitation) }} className={`${BUTTON} shrink-0`} aria-label={t.officeInvitation.revokeFor(invitation.email)}>{t.officeInvitation.revoke}</button></li>)}</ul>}
        {nextCursor !== null && <button type="button" disabled={busy} onClick={() => load(nextCursor, true)} className={`${BUTTON} mt-4`}>{t.officeInvitation.more}</button>}
      </div>
    </Dialogo>}
    {revoke && <Dialogo idTitulo="office-invitation-revoke" titulo={t.officeInvitation.revoke} onCerrar={() => { if (!busy) setRevoke(null) }}><p className="text-sm text-texto-suave">{t.officeInvitation.revokePrompt(revoke.email)}</p>{notice && <p role="alert" className="mt-3 text-sm text-peligro">{notice}</p>}<div className="mt-5 flex flex-wrap justify-end gap-2"><button type="button" disabled={busy} onClick={() => setRevoke(null)} className={BUTTON}>{t.officeInvitation.cancel}</button><button type="button" disabled={busy} onClick={confirmRevoke} className={`${TAMANO_BOTON_ACCION} rounded bg-peligro px-3 text-sobre-color disabled:opacity-60`}>{t.officeInvitation.revoke}</button></div></Dialogo>}
  </>
}
