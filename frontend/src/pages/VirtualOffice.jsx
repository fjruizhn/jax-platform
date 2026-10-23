import { useEffect, useRef, useState } from 'react'
import { useI18n } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
import Dialogo from '../components/Dialogo'
import { TAMANO_BOTON_ACCION, TAMANO_BOTON_44 } from '../tema/botones'
import { officeProjectsApi, officeSessionApi } from '../api/virtualOffice'
import OfficeInvitationManager from '../components/office/OfficeInvitationManager'
import OfficeProjectCreator from '../components/office/OfficeProjectCreator'
import OfficeProjectMembershipManager from '../components/office/OfficeProjectMembershipManager'
import OfficeAgentPanel from '../components/office/OfficeAgentPanel'

const PAGE_SIZE = 25
const validProject = (project) => project && Number.isInteger(project.id) && typeof project.name === 'string' && (typeof project.description === 'string' || project.description === null) && Number.isInteger(project.tenant_id)
const validList = (data) => data && Array.isArray(data.projects) && data.projects.every(validProject) && (data.next_after_id === null || Number.isInteger(data.next_after_id))
const validDetail = (data) => validProject(data) && Number.isInteger(data.scope_version) && typeof data.audience === 'string'

export default function VirtualOffice({ officeSession, onOfficeDenied }) {
  const { t } = useI18n()
  const user = useJaxStore((s) => s.user)
  const [showAccess, setShowAccess] = useState(false)
  const [projects, setProjects] = useState(null)
  const [nextAfterId, setNextAfterId] = useState(null)
  const [error, setError] = useState(false)
  const [detail, setDetail] = useState(null)
  const [detailError, setDetailError] = useState(false)
  const [pageLoading, setPageLoading] = useState(false)
  const [session, setSession] = useState(officeSession || null)
  const listGeneration = useRef(0)
  const detailGeneration = useRef(0)
  const listLoading = useRef(false)

  const loadProjects = async (afterId = null, append = false) => {
    if (listLoading.current) return
    listLoading.current = true
    const request = ++listGeneration.current
    setPageLoading(true)
    setError(false)
    if (!append) setProjects(null)
    try {
      const { data } = await officeProjectsApi.list({ limit: PAGE_SIZE, after_id: afterId })
      if (request !== listGeneration.current) return
      if (!validList(data)) throw new Error('invalid office projects response')
      setProjects((current) => append ? [...(current || []), ...data.projects] : data.projects)
      setNextAfterId(data.next_after_id)
    } catch (err) {
      if (request !== listGeneration.current) return
      if (err?.response?.status === 403) onOfficeDenied?.()
      setError(true)
    } finally {
      listLoading.current = false
      if (request === listGeneration.current) setPageLoading(false)
    }
  }

  useEffect(() => {
    loadProjects()
    // RequireOfficeSession validates entry. This independent, short-lived
    // capability read only controls which controls are displayed.
    if (!officeSession) officeSessionApi.read().then(({ data }) => {
      if (data && data.identity?.user_id === user?.user_id && data.capabilities) setSession(data)
    }).catch((err) => { if (err?.response?.status === 403) onOfficeDenied?.() })
    return () => { listGeneration.current += 1; detailGeneration.current += 1; listLoading.current = false }
  }, [])

  async function openDetail(projectId) {
    const request = ++detailGeneration.current
    setDetailError(false)
    setDetail({ loading: true })
    try {
      const { data } = await officeProjectsApi.detail(projectId)
      if (request !== detailGeneration.current) return
      if (!validDetail(data)) throw new Error('invalid office project detail')
      setDetail(data)
    } catch (err) {
      if (request !== detailGeneration.current) return
      if (err?.response?.status === 403) onOfficeDenied?.()
      setDetailError(true)
      setDetail({ failed: true })
    }
  }

  async function revalidateDetail(projectId) {
    const request = ++detailGeneration.current
    // A 403 invalidates the capability immediately.  The following detail
    // request is the only authority that can restore it.
    setDetail((current) => current && !current.loading && !current.failed
      ? { ...current, capabilities: { ...(current.capabilities || {}), manage_invitations: false } }
      : current)
    try {
      const { data } = await officeProjectsApi.detail(projectId)
      if (request !== detailGeneration.current) return
      if (!validDetail(data)) throw new Error('invalid office project detail')
      setDetail(data)
    } catch (err) {
      if (request !== detailGeneration.current) return
      if (err?.response?.status === 403) onOfficeDenied?.()
      setDetailError(true)
      setDetail({ failed: true })
    }
  }

  return (
    <main data-foco-inicial tabIndex={-1} className="min-h-dvh bg-fondo p-4 text-texto sm:p-6">
      <section className="mx-auto max-w-3xl space-y-5">
        <header className="rounded-xl border border-borde bg-superficie p-5">
          <p className="text-xs font-semibold uppercase tracking-widest text-acento-texto">{t.virtualOffice.kicker}</p>
          <h1 className="mt-1 text-2xl font-bold">{t.virtualOffice.title}</h1>
          <p className="mt-2 text-sm text-texto-suave">{t.virtualOffice.intro}</p>
          <dl className="mt-4 text-sm">
            <div><dt className="text-texto-tenue">{t.virtualOffice.account}</dt><dd className="break-all">{user?.email || t.virtualOffice.accountUnavailable}</dd></div>
          </dl>
          <button type="button" onClick={() => setShowAccess(true)} className={`${TAMANO_BOTON_ACCION} mt-4 rounded border border-borde text-texto hover:bg-hundido`}>{t.virtualOffice.accessInfo}</button>
        </header>

        <section className="rounded-xl border border-borde bg-superficie p-5">
          <h2 className="text-base font-semibold">{t.virtualOffice.projects.title}</h2>
          {session?.capabilities?.create_project === true && <OfficeProjectCreator tenantId={session.identity?.tenant_id} actorUserId={session.identity?.user_id} onForbidden={onOfficeDenied} onCreated={async (projectId) => { await loadProjects(); await openDetail(projectId) }} />}
          {projects === null && !error && <p role="status" className="mt-3 text-sm text-texto-suave">{t.virtualOffice.projects.loading}</p>}
          {error && <p role="alert" className="mt-3 text-sm text-peligro">{t.virtualOffice.projects.error}</p>}
          {projects?.length === 0 && <p role="status" className="mt-3 text-sm text-texto-suave">{t.virtualOffice.projects.empty}</p>}
          {projects?.length > 0 && <ul className="mt-3 divide-y divide-borde border-y border-borde">
            {projects.map((project) => <li key={project.id}><button type="button" onClick={() => openDetail(project.id)} className="w-full break-words py-3 text-left text-sm text-texto hover:bg-hundido focus:outline-none focus-visible:ring-2 focus-visible:ring-foco">{project.name}</button></li>)}
          </ul>}
          {nextAfterId !== null && <button type="button" disabled={pageLoading} aria-busy={pageLoading} onClick={() => loadProjects(nextAfterId, true)} className={`${TAMANO_BOTON_ACCION} mt-4 rounded border border-borde text-texto hover:bg-hundido disabled:opacity-60`}>{pageLoading ? t.virtualOffice.projects.loading : t.virtualOffice.projects.more}</button>}
        </section>
      </section>
      {showAccess && <Dialogo idTitulo="office-access-info" titulo={t.virtualOffice.accessTitle} onCerrar={() => setShowAccess(false)}>
        <p className="text-sm text-texto-suave">{t.virtualOffice.accessBody}</p>
        <div className="mt-5 flex justify-end"><button type="button" onClick={() => setShowAccess(false)} className={`${TAMANO_BOTON_ACCION} rounded bg-accion text-sobre-color`}>{t.virtualOffice.close}</button></div>
      </Dialogo>}
      {detail && <Dialogo idTitulo="office-project-detail" titulo={detail.loading || detail.failed ? t.virtualOffice.projects.detailTitle : detail.name} claseTitulo="text-sm font-semibold text-texto mb-4 break-words" className="max-w-lg max-h-[calc(100dvh-2rem)] flex flex-col" onCerrar={() => { detailGeneration.current += 1; setDetail(null); setDetailError(false) }}>
        <div className="min-h-0 overflow-y-auto break-words pr-1">
          {detail.loading && <p role="status" className="text-sm text-texto-suave">{t.virtualOffice.projects.loading}</p>}
          {detailError && <p role="alert" className="text-sm text-peligro">{t.virtualOffice.projects.error}</p>}
          {!detail.loading && !detail.failed && <>
            <p className="text-sm text-texto-suave whitespace-pre-wrap break-words">{detail.description || t.virtualOffice.projects.noDescription}</p>
            <OfficeAgentPanel projectId={detail.id} canManage={detail.capabilities?.manage_agents === true} onForbidden={() => revalidateDetail(detail.id)} />
            {detail.capabilities?.manage_invitations === true && <OfficeInvitationManager projectId={detail.id} onForbidden={() => revalidateDetail(detail.id)} />}
            {detail.capabilities?.manage_invitations === true && <OfficeProjectMembershipManager projectId={detail.id} canBrowsePeople={session?.capabilities?.administer_office === true} canAssignProjectAdmin={session.identity?.global_role === 'OFFICE_SUPERUSER'} onForbidden={() => revalidateDetail(detail.id)} />}
          </>}
        </div>
        <div className="mt-5 flex justify-end"><button type="button" onClick={() => { detailGeneration.current += 1; setDetail(null); setDetailError(false) }} className={`${TAMANO_BOTON_44} rounded bg-accion text-sobre-color`}>{t.virtualOffice.close}</button></div>
      </Dialogo>}
    </main>
  )
}
