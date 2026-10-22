import { useEffect, useRef, useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import Dialogo from '../Dialogo'
import { TAMANO_BOTON_ACCION } from '../../tema/botones'
import { officeMembersApi, officePeopleApi } from '../../api/virtualOffice'

const INPUT = 'w-full min-h-11 bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus-visible:ring-2 focus-visible:ring-foco'
const BUTTON = `${TAMANO_BOTON_ACCION} rounded border border-borde px-3 text-texto hover:bg-hundido disabled:opacity-60`
const validPeople = (data) => data && Array.isArray(data.people) && data.people.every((person) => Number.isInteger(person.user_id) && typeof person.email === 'string')
const validMember = (member) => member && Number.isInteger(member.user_id) && ['PROJECT_ADMIN', 'COLLABORATOR'].includes(member.grant_role) && ['INTERNAL', 'CLIENT_PUBLISHED'].includes(member.audience) && ['ACTIVE', 'REVOKED'].includes(member.status) && Number.isInteger(member.version) && member.version >= 1
const validMembers = (data, projectId) => data && data.project_id === projectId && Array.isArray(data.members) && data.members.every(validMember) && (data.next_cursor === null || Number.isInteger(data.next_cursor))

// The people endpoint is server-authorized and intentionally unavailable to a
// project-only administrator. Those administrators use the email invitation
// flow, which creates the recipient's membership when accepted.
export default function OfficeProjectMembershipManager({ projectId, canAssignProjectAdmin = false, onForbidden }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [people, setPeople] = useState(null)
  const [members, setMembers] = useState(null)
  const [nextCursor, setNextCursor] = useState(null)
  const [target, setTarget] = useState('')
  const [role, setRole] = useState('COLLABORATOR')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const intent = useRef(null)
  const membersLoading = useRef(false)

  useEffect(() => {
    if (!open) return
    let active = true
    setPeople(null); setMembers(null); setNextCursor(null); setError(null)
    officePeopleApi.list({ limit: 100 }).then(({ data }) => {
      if (active && validPeople(data)) setPeople(data.people)
      else if (active) setError(t.virtualOffice.members.error)
    }).catch(async (err) => {
      if (!active) return
      if (err?.response?.status === 403) {
        setError(t.virtualOffice.members.forbidden)
        await onForbidden?.()
      }
      else setError(t.virtualOffice.members.error)
    })

    async function loadMembers(cursor = 0, append = false) {
      if (membersLoading.current) return
      membersLoading.current = true
      try {
        const { data } = await officeMembersApi.list(projectId, { limit: 50, cursor })
        if (!active || !validMembers(data, projectId)) {
          if (active) setError(t.virtualOffice.members.error)
          return
        }
        setMembers((current) => append ? [...(current || []), ...data.members] : data.members)
        setNextCursor(data.next_cursor)
      } catch (err) {
        if (!active) return
        if (err?.response?.status === 403) {
          setError(t.virtualOffice.members.forbidden)
          await onForbidden?.()
        } else setError(t.virtualOffice.members.error)
      } finally { membersLoading.current = false }
    }
    loadMembers()
    return () => { active = false; membersLoading.current = false }
  }, [open, projectId])

  function close() { if (!busy) { intent.current = null; setOpen(false); setTarget(''); setRole('COLLABORATOR'); setError(null) } }
  async function grant(event) {
    event.preventDefault()
    const userId = Number(target)
    if (busy || !Number.isInteger(userId) || userId < 1) return
    // The endpoint repeats this authorization. This bit only keeps a general
    // Office administrator from being offered an operation the server rejects.
    const payload = { grant_role: role, audience: 'INTERNAL', expected_version: 0 }
    const signature = JSON.stringify({ userId, payload })
    if (intent.current?.signature !== signature) intent.current = { signature, key: crypto.randomUUID() }
    setBusy(true); setError(null)
    try { await officeMembersApi.grant(projectId, userId, payload, intent.current.key); intent.current = null; setOpen(false); setTarget(''); setRole('COLLABORATOR'); setError(null) }
    catch (err) {
      if (err?.response?.status === 403) {
        setError(t.virtualOffice.members.forbidden)
        await onForbidden?.()
        return
      }
      setError(err?.response?.status === 409 ? t.virtualOffice.members.conflict : t.virtualOffice.members.error)
    } finally { setBusy(false) }
  }

  async function mutateMember(member, operation, nextRole = member.grant_role) {
    if (busy) return
    const payload = { grant_role: nextRole, audience: member.audience, expected_version: member.version }
    const signature = JSON.stringify({ operation, userId: member.user_id, payload })
    if (intent.current?.signature !== signature) intent.current = { signature, key: crypto.randomUUID() }
    setBusy(true); setError(null)
    try {
      if (operation === 'revoke') await officeMembersApi.revoke(projectId, member.user_id, member.version, intent.current.key)
      else await officeMembersApi.grant(projectId, member.user_id, payload, intent.current.key)
      intent.current = null
      setMembers((current) => current && current.map((entry) => entry.user_id === member.user_id
        ? (operation === 'revoke' ? { ...entry, status: 'REVOKED', version: entry.version + 1 } : { ...entry, grant_role: nextRole, status: 'ACTIVE', version: entry.version + (entry.status === 'ACTIVE' && entry.grant_role === nextRole ? 0 : 1) })
        : entry))
    } catch (err) {
      if (err?.response?.status === 403) {
        setError(t.virtualOffice.members.forbidden)
        await onForbidden?.()
      } else setError(err?.response?.status === 409 ? t.virtualOffice.members.conflict : t.virtualOffice.members.error)
    } finally { setBusy(false) }
  }

  function personLabel(userId) {
    return people?.find((person) => person.user_id === userId)?.email || t.virtualOffice.members.userId(userId)
  }

  async function loadMoreMembers() {
    if (busy || nextCursor === null || membersLoading.current) return
    membersLoading.current = true
    setError(null)
    try {
      const { data } = await officeMembersApi.list(projectId, { limit: 50, cursor: nextCursor })
      if (!validMembers(data, projectId)) throw new Error('invalid office members response')
      setMembers((current) => [...(current || []), ...data.members])
      setNextCursor(data.next_cursor)
    } catch (err) {
      if (err?.response?.status === 403) {
        setError(t.virtualOffice.members.forbidden)
        await onForbidden?.()
      } else setError(t.virtualOffice.members.error)
    } finally { membersLoading.current = false }
  }

  return <>
    <button type="button" onClick={() => setOpen(true)} className={`${BUTTON} mt-3`}>{t.virtualOffice.members.open}</button>
    {open && <Dialogo idTitulo="office-project-members" titulo={t.virtualOffice.members.title} onCerrar={close}>
      <form onSubmit={grant} className="space-y-3">
        <p className="text-sm text-texto-suave">{t.virtualOffice.members.intro}</p>
        {people === null && !error && <p role="status" className="text-sm text-texto-suave">{t.virtualOffice.projects.loading}</p>}
        {people && <label className="block text-sm text-texto-suave" htmlFor="office-project-member">{t.virtualOffice.members.person}<select id="office-project-member" required disabled={busy} value={target} onChange={(event) => { setTarget(event.target.value); setError(null) }} className={`${INPUT} mt-1`}><option value="">{t.virtualOffice.members.choose}</option>{people.map((person) => <option key={person.user_id} value={person.user_id}>{person.email}</option>)}</select></label>}
        {canAssignProjectAdmin ? <label className="block text-sm text-texto-suave" htmlFor="office-project-member-role">{t.virtualOffice.members.role}<select id="office-project-member-role" disabled={busy} value={role} onChange={(event) => setRole(event.target.value)} className={`${INPUT} mt-1`}><option value="COLLABORATOR">{t.virtualOffice.members.roles.COLLABORATOR}</option><option value="PROJECT_ADMIN">{t.virtualOffice.members.roles.PROJECT_ADMIN}</option></select></label> : <p className="text-sm text-texto-suave">{t.virtualOffice.members.collaboratorOnly}</p>}
        {error && <p role="alert" className="text-sm text-peligro">{error}</p>}
        <div className="flex flex-wrap justify-end gap-2"><button type="button" disabled={busy} onClick={close} className={BUTTON}>{t.virtualOffice.cancel}</button><button disabled={busy || !target || !people} className={`${TAMANO_BOTON_ACCION} rounded bg-accion px-3 text-sobre-color disabled:opacity-60`}>{busy ? t.virtualOffice.members.granting : t.virtualOffice.members.grant}</button></div>
      </form>
      <section className="mt-6 border-t border-borde pt-4" aria-label={t.virtualOffice.members.currentTitle}>
        <h3 className="text-sm font-semibold text-texto">{t.virtualOffice.members.currentTitle}</h3>
        {members === null && !error && <p role="status" className="mt-2 text-sm text-texto-suave">{t.virtualOffice.members.loading}</p>}
        {members?.length === 0 && <p className="mt-2 text-sm text-texto-suave">{t.virtualOffice.members.empty}</p>}
        {members?.length > 0 && <ul className="mt-2 divide-y divide-borde border-y border-borde">{members.map((member) => <li key={member.user_id} className="py-3 text-sm"><p className="break-all text-texto">{personLabel(member.user_id)}</p><p className="text-texto-suave">{t.virtualOffice.members.audiences[member.audience]} · {t.virtualOffice.members.states[member.status]}</p><div className="mt-2 flex flex-wrap items-center gap-2">{canAssignProjectAdmin && member.audience === 'INTERNAL' ? <select aria-label={t.virtualOffice.members.roleFor(personLabel(member.user_id))} disabled={busy} defaultValue={member.grant_role} onChange={(event) => mutateMember(member, 'grant', event.target.value)} className={`${INPUT} w-auto`}><option value="COLLABORATOR">{t.virtualOffice.members.roles.COLLABORATOR}</option><option value="PROJECT_ADMIN">{t.virtualOffice.members.roles.PROJECT_ADMIN}</option></select> : <span className="text-texto-suave">{t.virtualOffice.members.roles[member.grant_role]}</span>}{member.status === 'ACTIVE' ? <button type="button" disabled={busy} onClick={() => mutateMember(member, 'revoke')} className={BUTTON}>{t.virtualOffice.members.revoke}</button> : <button type="button" disabled={busy} onClick={() => mutateMember(member, 'grant')} className={BUTTON}>{t.virtualOffice.members.restore}</button>}</div></li>)}</ul>}
        {nextCursor !== null && <button type="button" disabled={busy || membersLoading.current} onClick={loadMoreMembers} className={`${BUTTON} mt-3`}>{t.virtualOffice.members.more}</button>}
      </section>
    </Dialogo>}
  </>
}
