import { useEffect, useRef, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useI18n } from '../i18n/index.jsx'
import OfficeProjectCreator from '../components/office/OfficeProjectCreator'
import OfficeInvitationManager from '../components/office/OfficeInvitationManager'
import OfficeProjectMembershipManager from '../components/office/OfficeProjectMembershipManager'
import MarcaOficina from '../components/office/MarcaOficina'
import { officeProjectsApi } from '../api/virtualOffice'
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

export default function OfficeUsers({ officeSession, onOfficeDenied }) {
  const { t } = useI18n()
  const [projects, setProjects] = useState(null)
  const [nextAfterId, setNextAfterId] = useState(null)
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState(false)
  const [loading, setLoading] = useState(false)
  const listGeneration = useRef(0)
  const detailGeneration = useRef(0)
  const isInternal = ['INTERNAL', 'OFFICE_INTERNAL'].includes(officeSession?.identity?.account_class)
  const canCreate = officeSession?.capabilities?.create_project === true
  const isRoot = officeSession?.identity?.global_role === 'OFFICE_SUPERUSER'

  async function loadProjects(afterId = null, append = false) {
    const generation = ++listGeneration.current
    setLoading(true)
    setError(false)
    if (!append) { setProjects(null); setNextAfterId(null) }
    try {
      const { data } = await officeProjectsApi.list({ limit: PAGE_SIZE, after_id: afterId })
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
    return () => { listGeneration.current += 1; detailGeneration.current += 1 }
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
