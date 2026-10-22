import { useEffect, useRef, useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import Dialogo from '../Dialogo'
import { TAMANO_BOTON_ACCION } from '../../tema/botones'
import { officeMembersApi, officePeopleApi } from '../../api/virtualOffice'

const INPUT = 'w-full min-h-11 bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus-visible:ring-2 focus-visible:ring-foco'
const BUTTON = `${TAMANO_BOTON_ACCION} rounded border border-borde px-3 text-texto hover:bg-hundido disabled:opacity-60`
const validPeople = (data) => data && Array.isArray(data.people) && data.people.every((person) => Number.isInteger(person.user_id) && typeof person.email === 'string')

// The people endpoint is server-authorized and intentionally unavailable to a
// project-only administrator. Those administrators use the email invitation
// flow, which creates the recipient's membership when accepted.
export default function OfficeProjectMembershipManager({ projectId, onForbidden }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [people, setPeople] = useState(null)
  const [target, setTarget] = useState('')
  const [role, setRole] = useState('COLLABORATOR')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const intent = useRef(null)

  useEffect(() => {
    if (!open) return
    let active = true
    setPeople(null); setError(null)
    officePeopleApi.list({ limit: 100 }).then(({ data }) => {
      if (active && validPeople(data)) setPeople(data.people)
      else if (active) setError(t.virtualOffice.members.error)
    }).catch(async (err) => {
      if (!active) return
      if (err?.response?.status === 403) await onForbidden?.()
      else setError(t.virtualOffice.members.error)
    })
    return () => { active = false }
  }, [open, projectId])

  function close() { if (!busy) { intent.current = null; setOpen(false); setTarget(''); setRole('COLLABORATOR'); setError(null) } }
  async function grant(event) {
    event.preventDefault()
    const userId = Number(target)
    if (busy || !Number.isInteger(userId) || userId < 1) return
    const payload = { grant_role: role, audience: 'INTERNAL', expected_version: 0 }
    const signature = JSON.stringify({ userId, payload })
    if (intent.current?.signature !== signature) intent.current = { signature, key: crypto.randomUUID() }
    setBusy(true); setError(null)
    try { await officeMembersApi.grant(projectId, userId, payload, intent.current.key); intent.current = null; setOpen(false); setTarget(''); setRole('COLLABORATOR'); setError(null) }
    catch (err) {
      if (err?.response?.status === 403) { await onForbidden?.(); close(); return }
      setError(err?.response?.status === 409 ? t.virtualOffice.members.conflict : t.virtualOffice.members.error)
    } finally { setBusy(false) }
  }

  return <>
    <button type="button" onClick={() => setOpen(true)} className={`${BUTTON} mt-3`}>{t.virtualOffice.members.open}</button>
    {open && <Dialogo idTitulo="office-project-members" titulo={t.virtualOffice.members.title} onCerrar={close}>
      <form onSubmit={grant} className="space-y-3">
        <p className="text-sm text-texto-suave">{t.virtualOffice.members.intro}</p>
        {people === null && !error && <p role="status" className="text-sm text-texto-suave">{t.virtualOffice.projects.loading}</p>}
        {people && <label className="block text-sm text-texto-suave" htmlFor="office-project-member">{t.virtualOffice.members.person}<select id="office-project-member" required disabled={busy} value={target} onChange={(event) => { setTarget(event.target.value); setError(null) }} className={`${INPUT} mt-1`}><option value="">{t.virtualOffice.members.choose}</option>{people.map((person) => <option key={person.user_id} value={person.user_id}>{person.email}</option>)}</select></label>}
        <label className="block text-sm text-texto-suave" htmlFor="office-project-member-role">{t.virtualOffice.members.role}<select id="office-project-member-role" disabled={busy} value={role} onChange={(event) => setRole(event.target.value)} className={`${INPUT} mt-1`}><option value="COLLABORATOR">{t.virtualOffice.members.roles.COLLABORATOR}</option><option value="PROJECT_ADMIN">{t.virtualOffice.members.roles.PROJECT_ADMIN}</option></select></label>
        {error && <p role="alert" className="text-sm text-peligro">{error}</p>}
        <div className="flex flex-wrap justify-end gap-2"><button type="button" disabled={busy} onClick={close} className={BUTTON}>{t.virtualOffice.cancel}</button><button disabled={busy || !target || !people} className={`${TAMANO_BOTON_ACCION} rounded bg-accion px-3 text-sobre-color disabled:opacity-60`}>{busy ? t.virtualOffice.members.granting : t.virtualOffice.members.grant}</button></div>
      </form>
    </Dialogo>}
  </>
}
