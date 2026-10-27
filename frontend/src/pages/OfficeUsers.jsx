import { useEffect, useRef, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useI18n } from '../i18n/index.jsx'
import OfficeProjectCreator from '../components/office/OfficeProjectCreator'
import OfficeInvitationManager from '../components/office/OfficeInvitationManager'
import OfficeProjectMembershipManager from '../components/office/OfficeProjectMembershipManager'
import MarcaOficina from '../components/office/MarcaOficina'
import Dialogo from '../components/Dialogo'
import { officeAccountInvitationsApi, officePeopleApi, officeProjectsApi, officeMembersApi } from '../api/virtualOffice'
import { esInstalacionDeOficina } from '../lib/instalacionOficina'
import { TAMANO_BOTON_ACCION } from '../tema/botones'

const PAGE_SIZE = 25
const BUTTON = `${TAMANO_BOTON_ACCION} rounded border border-borde px-3 text-texto hover:bg-hundido disabled:opacity-60`
const validProject = (project) => project && Number.isInteger(project.id) && typeof project.name === 'string'
  && Number.isInteger(project.tenant_id)
const validList = (data) => data && Array.isArray(data.projects) && data.projects.every(validProject)
  && (data.next_after_id === null || Number.isInteger(data.next_after_id))
const validDetail = (data) => validProject(data) && Number.isInteger(data.scope_version)
  && typeof data.audience === 'string'
const validPeople = (data) => data && Array.isArray(data.people)
  && data.people.every((person) => Number.isInteger(person.user_id) && typeof person.email === 'string')
  && (data.next_after_user_id === null || Number.isInteger(data.next_after_user_id))
const validPersonProjects = (data, userId) => data && data.user_id === userId && Array.isArray(data.projects)
  && data.projects.every((project) => Number.isInteger(project.project_id) && typeof project.name === 'string'
    && Number.isInteger(project.version))
  && (data.next_after_project_id === null || Number.isInteger(data.next_after_project_id))
const INPUT = 'min-h-11 rounded-lg border border-borde-control bg-hundido px-3 py-2 text-texto focus-visible:ring-2 focus-visible:ring-foco'

export default function OfficeUsers({ officeSession, onOfficeDenied }) {
  const { t } = useI18n()
  const [projects, setProjects] = useState(null)
  const [nextAfterId, setNextAfterId] = useState(null)
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState(false)
  const [loading, setLoading] = useState(false)
  const [people, setPeople] = useState(null)
  const [peopleError, setPeopleError] = useState(false)
  const [peopleNext, setPeopleNext] = useState(null)
  const [person, setPerson] = useState(null)
  const [personProjects, setPersonProjects] = useState(null)
  const [personProjectsNext, setPersonProjectsNext] = useState(null)
  const [personError, setPersonError] = useState(false)
  const [email, setEmail] = useState('')
  const [inviteBusy, setInviteBusy] = useState(false)
  const [inviteNotice, setInviteNotice] = useState(null)
  const [accountInvites, setAccountInvites] = useState(null)
  const [accountInvitesNext, setAccountInvitesNext] = useState(null)
  const [accountInvitesError, setAccountInvitesError] = useState(false)
  const [revokeInvite, setRevokeInvite] = useState(null)
  const [revokeBusy, setRevokeBusy] = useState(false)
  const [assignProject, setAssignProject] = useState('')
  const [assignRole, setAssignRole] = useState('COLLABORATOR')
  const [assignBusy, setAssignBusy] = useState(false)
  const [assignError, setAssignError] = useState(false)
  const [removeProject, setRemoveProject] = useState(null)
  const [removeBusy, setRemoveBusy] = useState(false)
  const [removeError, setRemoveError] = useState(false)
  const peopleGeneration = useRef(0)
  const personGeneration = useRef(0)
  const invitationIntent = useRef(null)
  const assignmentIntent = useRef(null)
  const removalIntent = useRef(null)
  const invitesGeneration = useRef(0)
  const revokeIntent = useRef(null)
  const listGeneration = useRef(0)
  const detailGeneration = useRef(0)
  const isInternal = ['INTERNAL', 'OFFICE_INTERNAL'].includes(officeSession?.identity?.account_class)
  const canCreate = officeSession?.capabilities?.create_project === true
  const isRoot = officeSession?.identity?.global_role === 'OFFICE_SUPERUSER'
  const canBrowsePeople = officeSession?.capabilities?.browse_people === true || isRoot
  const canInvitePeople = officeSession?.capabilities?.invite_people === true || isRoot

  async function loadPeople(afterId = null, append = false) {
    const generation = ++peopleGeneration.current
    setPeopleError(false)
    if (!append) { setPeople(null); setPeopleNext(null) }
    try {
      const { data } = await officePeopleApi.list({ limit: 50, after_user_id: afterId })
      if (generation !== peopleGeneration.current) return
      if (!validPeople(data)) throw new Error('invalid office people list')
      setPeople((current) => append ? [...(current || []), ...data.people] : data.people)
      setPeopleNext(data.next_after_user_id)
    } catch (err) {
      if (generation !== peopleGeneration.current) return
      if (err?.response?.status === 403) onOfficeDenied?.()
      setPeopleError(true)
    }
  }

  async function loadPersonProjects(target, afterId = 0, append = false) {
    const generation = ++personGeneration.current
    setPersonError(false)
    if (!append) { setPersonProjects(null); setPersonProjectsNext(null) }
    try {
      const { data } = await officePeopleApi.projects(target.user_id, { limit: 50, after_project_id: afterId })
      if (generation !== personGeneration.current) return
      if (!validPersonProjects(data, target.user_id)) throw new Error('invalid office person projects')
      setPersonProjects((current) => append ? [...(current || []), ...data.projects] : data.projects)
      setPersonProjectsNext(data.next_after_project_id)
    } catch (err) {
      if (generation !== personGeneration.current) return
      if (err?.response?.status === 403) onOfficeDenied?.()
      setPersonError(true)
    }
  }

  function selectPerson(target) {
    setPerson(target); setAssignProject(''); setAssignError(false)
    loadPersonProjects(target)
  }

  async function invitePerson(event) {
    event.preventDefault()
    if (inviteBusy || !email.trim()) return
    const normalized = email.trim()
    if (invitationIntent.current?.email !== normalized) invitationIntent.current = { email: normalized, key: crypto.randomUUID() }
    setInviteBusy(true); setInviteNotice(null)
    try {
      await officeAccountInvitationsApi.issue(normalized, invitationIntent.current.key)
      invitationIntent.current = null; setEmail(''); setInviteNotice('sent'); await loadAccountInvites()
    } catch (err) {
      setInviteNotice(err?.response?.status === 409 ? 'conflict' : 'error')
    } finally { setInviteBusy(false) }
  }

  async function loadAccountInvites(afterId = null, append = false) {
    const generation = ++invitesGeneration.current
    setAccountInvitesError(false)
    if (!append) { setAccountInvites(null); setAccountInvitesNext(null) }
    try {
      const { data } = await officeAccountInvitationsApi.list({ limit: 25, after_id: afterId })
      if (generation !== invitesGeneration.current) return
      if (!data || !Array.isArray(data.invitations) ||
          !data.invitations.every((entry) => typeof entry.invitation_id === 'string' && typeof entry.email === 'string' && Number.isInteger(entry.version)) ||
          (data.next_after_id !== null && typeof data.next_after_id !== 'string')) throw new Error('invalid account invitations')
      setAccountInvites((current) => append ? [...(current || []), ...data.invitations] : data.invitations)
      setAccountInvitesNext(data.next_after_id)
    } catch (err) {
      if (generation !== invitesGeneration.current) return
      if (err?.response?.status === 403) onOfficeDenied?.()
      setAccountInvitesError(true)
    }
  }

  async function revokeAccountInvite() {
    if (!revokeInvite || revokeBusy) return
    const signature = JSON.stringify({ id: revokeInvite.invitation_id, version: revokeInvite.version })
    if (revokeIntent.current?.signature !== signature) revokeIntent.current = { signature, key: crypto.randomUUID() }
    setRevokeBusy(true); setAccountInvitesError(false)
    try {
      await officeAccountInvitationsApi.revoke(revokeInvite.invitation_id, revokeInvite.version, revokeIntent.current.key)
      revokeIntent.current = null; setRevokeInvite(null); await loadAccountInvites()
    } catch (err) {
      if (err?.response?.status === 403) onOfficeDenied?.()
      setAccountInvitesError(true)
    } finally { setRevokeBusy(false) }
  }

  async function assignPerson(event) {
    event.preventDefault()
    const projectId = Number(assignProject)
    if (!person || !Number.isInteger(projectId) || assignBusy) return
    const payload = { grant_role: assignRole, audience: 'INTERNAL', expected_version: 0 }
    const signature = JSON.stringify({ userId: person.user_id, projectId, payload })
    if (assignmentIntent.current?.signature !== signature) assignmentIntent.current = { signature, key: crypto.randomUUID() }
    setAssignBusy(true); setAssignError(false)
    try {
      await officeMembersApi.grant(projectId, person.user_id, payload, assignmentIntent.current.key)
      assignmentIntent.current = null; setAssignProject(''); await loadPersonProjects(person)
    } catch (err) {
      if (err?.response?.status === 403) onOfficeDenied?.()
      setAssignError(true)
    } finally { setAssignBusy(false) }
  }

  async function removePersonProject() {
    if (!person || !removeProject || removeBusy) return
    const signature = JSON.stringify({ userId: person.user_id, projectId: removeProject.project_id, version: removeProject.version })
    if (removalIntent.current?.signature !== signature) removalIntent.current = { signature, key: crypto.randomUUID() }
    setRemoveBusy(true); setRemoveError(false)
    try {
      await officeMembersApi.revoke(removeProject.project_id, person.user_id, removeProject.version, removalIntent.current.key)
      removalIntent.current = null; setRemoveProject(null); await loadPersonProjects(person)
    } catch (err) {
      if (err?.response?.status === 403) onOfficeDenied?.()
      setRemoveError(true)
    } finally { setRemoveBusy(false) }
  }

  async function loadProjects(afterId = null, append = false) {
    const generation = ++listGeneration.current
    setLoading(true)
    setError(false)
    if (!append) { setProjects(null); setNextAfterId(null) }
    try {
      const { data } = !isRoot && canBrowsePeople
        ? await officePeopleApi.assignableProjects({ limit: PAGE_SIZE, after_project_id: afterId || 0 })
        : await officeProjectsApi.list({ limit: PAGE_SIZE, after_id: afterId })
      if (generation !== listGeneration.current) return
      if (!validList(data)) throw new Error('invalid office project list')
      setProjects((current) => append ? [...(current || []), ...data.projects] : data.projects)
      setNextAfterId(data.next_after_id)
    } catch (err) {
      if (generation !== listGeneration.current) return
      if (err?.response?.status === 403) onOfficeDenied?.()
      setError(true)
    } finally {
      if (generation === listGeneration.current) setLoading(false)
    }
  }

  async function openProject(projectId) {
    const generation = ++detailGeneration.current
    setError(false)
    setSelected({ loading: true })
    try {
      if (!isRoot && canBrowsePeople) {
        const project = projects?.find((entry) => entry.id === projectId)
        if (!project) throw new Error('invalid office project selection')
        setSelected({ ...project, capabilities: { manage_invitations: true } })
        return
      }
      const { data } = await officeProjectsApi.detail(projectId)
      if (generation !== detailGeneration.current) return
      if (!validDetail(data)) throw new Error('invalid office project detail')
      setSelected(data)
      setError(false)
    } catch (err) {
      if (generation !== detailGeneration.current) return
      if (err?.response?.status === 403) onOfficeDenied?.()
      setSelected(null)
      setError(true)
    }
  }

  useEffect(() => {
    if (isInternal) loadProjects()
    if (isInternal && canBrowsePeople) loadPeople()
    if (isInternal && canInvitePeople) loadAccountInvites()
    return () => { listGeneration.current += 1; detailGeneration.current += 1; peopleGeneration.current += 1; personGeneration.current += 1; invitesGeneration.current += 1 }
  }, [isInternal])

  if (!isInternal) return <Navigate to="/virtual-office" replace />

  return <main data-foco-inicial tabIndex={-1} className="min-h-dvh bg-fondo p-4 text-texto sm:p-6">
    <div className="mx-auto max-w-3xl space-y-5">
      <header className="tarjeta-oficina">
        {esInstalacionDeOficina() && <MarcaOficina etiqueta={t.virtualOffice.title} />}
        <h1 className="mt-3 text-2xl font-bold">{t.virtualOffice.userAdmin.title}</h1>
        <p className="mt-2 text-sm text-texto-suave">{t.virtualOffice.userAdmin.intro}</p>
        <Link to="/virtual-office" className={`${BUTTON} mt-4 inline-flex items-center`}>{t.virtualOffice.userAdmin.back}</Link>
      </header>

      {canBrowsePeople && <section className="tarjeta-oficina" aria-labelledby="office-human-users-title">
        <h2 id="office-human-users-title" className="text-lg font-semibold">{t.virtualOffice.userAdmin.people}</h2>
        {people === null && !peopleError && <p role="status" className="mt-3 text-sm text-texto-suave">{t.virtualOffice.projects.loading}</p>}
        {peopleError && <p role="alert" className="mt-3 text-sm text-peligro">{t.virtualOffice.userAdmin.peopleError}</p>}
        {people?.length === 0 && <p className="mt-3 text-sm text-texto-suave">{t.virtualOffice.userAdmin.peopleEmpty}</p>}
        {people?.length > 0 && <ul className="mt-3 divide-y divide-borde border-y border-borde">{people.map((entry) =>
          <li key={entry.user_id}><button type="button" onClick={() => selectPerson(entry)} className="flex min-h-11 w-full flex-wrap items-center justify-between gap-2 py-2 text-left hover:text-acento-texto">
            <span className="break-all">{entry.email}</span><span className="text-sm text-texto-suave">{t.virtualOffice.userAdmin.roles[entry.global_role]}</span>
          </button></li>)}</ul>}
        {peopleNext !== null && <button type="button" className={`${BUTTON} mt-3`} onClick={() => loadPeople(peopleNext, true)}>{t.virtualOffice.projects.more}</button>}
        {person && <div className="mt-5 border-t border-borde pt-4">
          <h3 className="break-all font-semibold">{person.email}</h3>
          <p className="mt-1 text-sm text-texto-suave">{t.virtualOffice.userAdmin.roles[person.global_role]}</p>
          {person.user_id === officeSession.identity.user_id && <Link to="/account/security" className="mt-2 inline-flex min-h-11 items-center text-acento-texto underline">{t.virtualOffice.userAdmin.myMfa}</Link>}
          <h4 className="mt-4 font-medium">{t.virtualOffice.userAdmin.participates}</h4>
          {personProjects === null && !personError && <p role="status" className="mt-2 text-sm text-texto-suave">{t.virtualOffice.projects.loading}</p>}
          {personError && <p role="alert" className="mt-2 text-sm text-peligro">{t.virtualOffice.userAdmin.projectsError}</p>}
          {personProjects?.length === 0 && <p className="mt-2 text-sm text-texto-suave">{t.virtualOffice.userAdmin.noProjects}</p>}
          {personProjects?.length > 0 && <ul className="mt-2 divide-y divide-borde">{personProjects.map((entry) => <li key={entry.project_id} className="flex flex-wrap items-center justify-between gap-2 py-2"><span>{entry.name}</span><span className="flex items-center gap-2 text-sm text-texto-suave">{t.virtualOffice.members.roles[entry.grant_role]}<button type="button" className={BUTTON} onClick={() => { setRemoveError(false); setRemoveProject(entry) }}>{t.virtualOffice.userAdmin.remove}</button></span></li>)}</ul>}
          {personProjectsNext !== null && <button type="button" className={`${BUTTON} mt-2`} onClick={() => loadPersonProjects(person, personProjectsNext, true)}>{t.virtualOffice.projects.more}</button>}
          {person.account_class !== 'EXTERNAL_CLIENT' && projects?.some((project) => !personProjects?.some((entry) => entry.project_id === project.id)) && <form onSubmit={assignPerson} className="mt-4 flex flex-wrap items-end gap-2">
            <label className="text-sm">{t.virtualOffice.userAdmin.assignProject}<select required className={`${INPUT} mt-1 block`} value={assignProject} onChange={(event) => setAssignProject(event.target.value)}><option value="">{t.virtualOffice.members.choose}</option>{projects.filter((project) => !personProjects?.some((entry) => entry.project_id === project.id)).map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
            {isRoot && <label className="text-sm">{t.virtualOffice.members.role}<select className={`${INPUT} mt-1 block`} value={assignRole} onChange={(event) => setAssignRole(event.target.value)}><option value="COLLABORATOR">{t.virtualOffice.members.roles.COLLABORATOR}</option><option value="PROJECT_ADMIN">{t.virtualOffice.members.roles.PROJECT_ADMIN}</option></select></label>}
            <button type="submit" disabled={assignBusy || !assignProject} className={BUTTON}>{t.virtualOffice.userAdmin.assign}</button>
          </form>}
          {assignError && <p role="alert" className="mt-2 text-sm text-peligro">{t.virtualOffice.userAdmin.assignError}</p>}
        </div>}
      </section>}

      {removeProject && <Dialogo idTitulo="office-person-remove-project" titulo={t.virtualOffice.userAdmin.removeTitle} onCerrar={() => { if (!removeBusy) setRemoveProject(null) }} cerrable={!removeBusy}>
        <p className="break-words text-sm text-texto-suave">{t.virtualOffice.userAdmin.removePrompt(person.email, removeProject.name)}</p>
        {removeError && <p role="alert" className="mt-2 text-sm text-peligro">{t.virtualOffice.userAdmin.removeError}</p>}
        <div className="mt-4 flex justify-end gap-2"><button type="button" disabled={removeBusy} className={BUTTON} onClick={() => setRemoveProject(null)}>{t.virtualOffice.cancel}</button><button type="button" disabled={removeBusy} className={BUTTON} onClick={removePersonProject}>{t.virtualOffice.userAdmin.remove}</button></div>
      </Dialogo>}

      {canInvitePeople && <section className="tarjeta-oficina" aria-labelledby="office-invite-person-title">
        <h2 id="office-invite-person-title" className="text-lg font-semibold">{t.virtualOffice.userAdmin.invite}</h2>
        <p className="mt-2 text-sm text-texto-suave">{t.virtualOffice.userAdmin.inviteIntro}</p>
        <form onSubmit={invitePerson} className="mt-3 flex flex-wrap items-end gap-2"><label className="flex-1 text-sm">{t.virtualOffice.userAdmin.email}<input className={`${INPUT} mt-1 w-full`} type="email" required autoComplete="email" disabled={inviteBusy} value={email} onChange={(event) => setEmail(event.target.value)} /></label><button type="submit" disabled={inviteBusy} className={BUTTON}>{t.virtualOffice.userAdmin.sendInvite}</button></form>
        {inviteNotice && <p role="status" className="mt-2 text-sm text-texto-suave">{t.virtualOffice.userAdmin.inviteNotices[inviteNotice]}</p>}
        <h3 className="mt-5 font-medium">{t.virtualOffice.userAdmin.sentInvites}</h3>
        {accountInvites === null && !accountInvitesError && <p role="status" className="mt-2 text-sm text-texto-suave">{t.virtualOffice.projects.loading}</p>}
        {accountInvitesError && <p role="alert" className="mt-2 text-sm text-peligro">{t.virtualOffice.userAdmin.invitesError}</p>}
        {accountInvites?.length === 0 && <p className="mt-2 text-sm text-texto-suave">{t.virtualOffice.userAdmin.noInvites}</p>}
        {accountInvites?.length > 0 && <ul className="mt-2 divide-y divide-borde">{accountInvites.map((entry) => <li key={entry.invitation_id} className="flex flex-wrap items-center justify-between gap-2 py-2"><span className="break-all">{entry.email}</span><span className="flex flex-wrap items-center gap-2 text-sm text-texto-suave">{t.virtualOffice.userAdmin.inviteStates[entry.status]} · {t.virtualOffice.userAdmin.deliveryStates[entry.delivery_state]}{entry.status === 'ISSUED' && <button type="button" className={BUTTON} onClick={() => setRevokeInvite(entry)}>{t.virtualOffice.userAdmin.revokeInvite}</button>}</span></li>)}</ul>}
        {accountInvitesNext !== null && <button type="button" className={`${BUTTON} mt-3`} onClick={() => loadAccountInvites(accountInvitesNext, true)}>{t.virtualOffice.projects.more}</button>}
      </section>}

      {revokeInvite && <Dialogo idTitulo="office-account-invitation-revoke" titulo={t.virtualOffice.userAdmin.revokeInvite} onCerrar={() => { if (!revokeBusy) setRevokeInvite(null) }} cerrable={!revokeBusy}>
        <p className="break-words text-sm text-texto-suave">{t.virtualOffice.userAdmin.revokeInvitePrompt(revokeInvite.email)}</p>
        <div className="mt-4 flex justify-end gap-2"><button type="button" disabled={revokeBusy} className={BUTTON} onClick={() => setRevokeInvite(null)}>{t.virtualOffice.cancel}</button><button type="button" disabled={revokeBusy} className={BUTTON} onClick={revokeAccountInvite}>{t.virtualOffice.userAdmin.revokeInvite}</button></div>
      </Dialogo>}

      <section className="tarjeta-oficina" aria-labelledby="office-user-projects-title">
        <h2 id="office-user-projects-title" className="text-lg font-semibold">{t.virtualOffice.userAdmin.projects}</h2>
        {canCreate && <OfficeProjectCreator tenantId={officeSession.identity.tenant_id} actorUserId={officeSession.identity.user_id} onForbidden={onOfficeDenied} onCreated={async (projectId) => { await loadProjects(); await openProject(projectId) }} />}
        {projects === null && !error && <p role="status" className="mt-3 text-sm text-texto-suave">{t.virtualOffice.projects.loading}</p>}
        {error && <p role="alert" className="mt-3 text-sm text-peligro">{t.virtualOffice.projects.error}</p>}
        {projects?.length === 0 && <p className="mt-3 text-sm text-texto-suave">{t.virtualOffice.userAdmin.empty}</p>}
        {projects?.length > 0 && <ul className="mt-3 divide-y divide-borde border-y border-borde">
          {projects.map((project) => <li key={project.id}><button type="button" className="w-full min-h-11 break-words py-2 text-left text-texto hover:text-acento-texto" onClick={() => openProject(project.id)}>{project.name}</button></li>)}
        </ul>}
        {nextAfterId !== null && <button type="button" className={`${BUTTON} mt-3`} disabled={loading} onClick={() => loadProjects(nextAfterId, true)}>{t.virtualOffice.projects.more}</button>}
      </section>

      {selected && <section className="tarjeta-oficina" aria-labelledby="office-selected-project-title">
        <h2 id="office-selected-project-title" className="text-lg font-semibold">{selected.loading ? t.virtualOffice.projects.loading : selected.name}</h2>
        {selected.loading && <p role="status" className="text-sm text-texto-suave">{t.virtualOffice.projects.loading}</p>}
        {!selected.loading && selected.capabilities?.manage_invitations === true && <>
          <OfficeInvitationManager projectId={selected.id} onForbidden={() => openProject(selected.id)} />
          <OfficeProjectMembershipManager projectId={selected.id} canBrowsePeople={officeSession.capabilities?.administer_office === true} canAssignProjectAdmin={isRoot} onForbidden={() => openProject(selected.id)} />
        </>}
        {!selected.loading && selected.capabilities?.manage_invitations !== true && <p className="text-sm text-texto-suave">{t.virtualOffice.userAdmin.noPermission}</p>}
      </section>}
    </div>
  </main>
}
