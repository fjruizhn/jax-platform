import { useRef, useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import { officeInvitationsApi } from '../../api/virtualOffice'
import Dialogo from '../Dialogo'
import { TAMANO_BOTON_ACCION } from '../../tema/botones'

const INPUT = 'w-full min-h-11 bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus-visible:ring-2 focus-visible:ring-foco'
const BUTTON = `${TAMANO_BOTON_ACCION} rounded border border-borde px-3 text-texto hover:bg-hundido disabled:opacity-60`

// Este componente queda deliberadamente inerte hasta que la pantalla de
// proyecto reciba el contrato aprobado de invitaciones (incluida su lista).
// No infiere invitaciones ni consulta endpoints no contratados.
export default function OfficeInvitationManager({ projectId, invitations = [], enabled = false, onChanged }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [email, setEmail] = useState('')
  const [audience, setAudience] = useState('INTERNAL')
  const [revoke, setRevoke] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const issueKey = useRef(null)
  const revokeKey = useRef(null)

  if (!enabled) return null

  async function issue(event) {
    event.preventDefault()
    if (busy || !email.trim()) return
    issueKey.current ??= crypto.randomUUID()
    setBusy(true)
    setError(null)
    try {
      await officeInvitationsApi.issue(projectId, { email: email.trim(), audience }, issueKey.current)
      issueKey.current = null
      setEmail('')
      await onChanged?.()
    } catch {
      setError(t.officeInvitation.managerError)
    } finally {
      setBusy(false)
    }
  }

  async function confirmRevoke() {
    if (!revoke || busy) return
    revokeKey.current ??= crypto.randomUUID()
    setBusy(true)
    setError(null)
    try {
      await officeInvitationsApi.revoke(projectId, revoke.id, revoke.version, revokeKey.current)
      revokeKey.current = null
      setRevoke(null)
      await onChanged?.()
    } catch {
      setError(t.officeInvitation.managerError)
    } finally {
      setBusy(false)
    }
  }

  return <>
    <button type="button" onClick={() => setOpen(true)} className={BUTTON}>{t.officeInvitation.manage}</button>
    {open && <Dialogo idTitulo="office-invitations" titulo={t.officeInvitation.manage} onCerrar={() => { if (!busy) setOpen(false) }}>
      <form onSubmit={issue} className="space-y-3">
        <label className="block text-sm text-texto-suave" htmlFor="office-invitation-email">{t.officeInvitation.email}
          <input id="office-invitation-email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" required className={`${INPUT} mt-1`} />
        </label>
        <label className="block text-sm text-texto-suave" htmlFor="office-invitation-audience">{t.officeInvitation.audience}
          <select id="office-invitation-audience" value={audience} onChange={(event) => setAudience(event.target.value)} className={`${INPUT} mt-1`}>
            <option value="INTERNAL">{t.officeInvitation.audiences.INTERNAL}</option>
            <option value="CLIENT_PUBLISHED">{t.officeInvitation.audiences.CLIENT_PUBLISHED}</option>
          </select>
        </label>
        {error && <p role="alert" className="text-sm text-peligro">{error}</p>}
        <button disabled={busy} className={`${TAMANO_BOTON_ACCION} w-full rounded bg-accion text-sobre-color disabled:opacity-60`}>{t.officeInvitation.send}</button>
      </form>
      {invitations.length > 0 && <ul className="mt-5 divide-y divide-borde border-y border-borde">
        {invitations.map((invitation) => <li key={invitation.id} className="flex items-center justify-between gap-3 py-3">
          <span className="min-w-0 break-all text-sm text-texto">{invitation.email}</span>
          <button type="button" disabled={busy} onClick={() => { revokeKey.current = null; setRevoke(invitation) }} className={BUTTON} aria-label={t.officeInvitation.revokeFor(invitation.email)}>{t.officeInvitation.revoke}</button>
        </li>)}
      </ul>}
    </Dialogo>}
    {revoke && <Dialogo idTitulo="office-invitation-revoke" titulo={t.officeInvitation.revoke} onCerrar={() => { if (!busy) setRevoke(null) }}>
      <p className="text-sm text-texto-suave">{t.officeInvitation.revokePrompt(revoke.email)}</p>
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        <button type="button" disabled={busy} onClick={() => setRevoke(null)} className={BUTTON}>{t.officeInvitation.cancel}</button>
        <button type="button" disabled={busy} onClick={confirmRevoke} className={`${TAMANO_BOTON_ACCION} rounded bg-peligro px-3 text-sobre-color disabled:opacity-60`}>{t.officeInvitation.revoke}</button>
      </div>
    </Dialogo>}
  </>
}
