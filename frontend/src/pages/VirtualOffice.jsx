import { useRef, useState } from 'react'
import { useI18n } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
import Dialogo from '../components/Dialogo'
import { TAMANO_BOTON_ACCION } from '../tema/botones'

const PANELES = ['projects', 'people', 'grants']

function roleLabel(user, t) {
  const role = user?.office_role
  return t.virtualOffice.roles[role] || t.virtualOffice.roles.unknown
}

function UnavailableAction({ children }) {
  return <button type="button" disabled aria-disabled="true" className={`${TAMANO_BOTON_ACCION} rounded border border-borde text-texto-tenue disabled:opacity-60`}>{children}</button>
}

function Panel({ tab, t }) {
  if (tab === 'projects') return (
    <section role="tabpanel" id="office-panel-projects" aria-labelledby="office-tab-projects" className="space-y-4">
      <h2 className="text-base font-semibold">{t.virtualOffice.projects.title}</h2>
      <p role="status" className="rounded-lg border border-borde bg-hundido p-3 text-sm text-texto-suave">{t.virtualOffice.projects.unavailable}</p>
      <UnavailableAction>{t.virtualOffice.projects.select}</UnavailableAction>
    </section>
  )
  if (tab === 'people') return (
    <section role="tabpanel" id="office-panel-people" aria-labelledby="office-tab-people" className="space-y-4">
      <h2 className="text-base font-semibold">{t.virtualOffice.people.title}</h2>
      <p role="status" className="rounded-lg border border-borde bg-hundido p-3 text-sm text-texto-suave">{t.virtualOffice.people.unavailable}</p>
      <UnavailableAction>{t.virtualOffice.people.invite}</UnavailableAction>
    </section>
  )
  return (
    <section role="tabpanel" id="office-panel-grants" aria-labelledby="office-tab-grants" className="space-y-4">
      <h2 className="text-base font-semibold">{t.virtualOffice.grants.title}</h2>
      <p role="status" className="rounded-lg border border-borde bg-hundido p-3 text-sm text-texto-suave">{t.virtualOffice.grants.unavailable}</p>
      <UnavailableAction>{t.virtualOffice.grants.grant}</UnavailableAction>
    </section>
  )
}

export default function VirtualOffice() {
  const { t } = useI18n()
  const user = useJaxStore((s) => s.user)
  const [tab, setTab] = useState('projects')
  const [showAccess, setShowAccess] = useState(false)
  const refs = useRef({})

  function select(next) {
    setTab(next)
    refs.current[next]?.focus()
  }

  function onKeyDown(event, current) {
    const index = PANELES.indexOf(current)
    if (event.key === 'ArrowRight') { event.preventDefault(); select(PANELES[(index + 1) % PANELES.length]) }
    if (event.key === 'ArrowLeft') { event.preventDefault(); select(PANELES[(index + PANELES.length - 1) % PANELES.length]) }
    if (event.key === 'Home') { event.preventDefault(); select(PANELES[0]) }
    if (event.key === 'End') { event.preventDefault(); select(PANELES.at(-1)) }
  }

  return (
    <main data-foco-inicial tabIndex={-1} className="min-h-dvh bg-fondo p-4 text-texto sm:p-6">
      <section className="mx-auto max-w-3xl space-y-5">
        <header className="rounded-xl border border-borde bg-superficie p-5">
          <p className="text-xs font-semibold uppercase tracking-widest text-acento-texto">{t.virtualOffice.kicker}</p>
          <h1 className="mt-1 text-2xl font-bold">{t.virtualOffice.title}</h1>
          <p className="mt-2 text-sm text-texto-suave">{t.virtualOffice.intro}</p>
          <dl className="mt-4 grid gap-2 text-sm sm:grid-cols-2">
            <div><dt className="text-texto-tenue">{t.virtualOffice.account}</dt><dd className="break-all">{user?.email || t.virtualOffice.accountUnavailable}</dd></div>
            <div><dt className="text-texto-tenue">{t.virtualOffice.role}</dt><dd>{roleLabel(user, t)}</dd></div>
          </dl>
          <button type="button" onClick={() => setShowAccess(true)} className={`${TAMANO_BOTON_ACCION} mt-4 rounded border border-borde text-texto hover:bg-hundido`}>{t.virtualOffice.accessInfo}</button>
        </header>

        <div role="tablist" aria-label={t.virtualOffice.sections} className="flex gap-2 overflow-x-auto border-b border-borde">
          {PANELES.map((name) => <button
            key={name} ref={(node) => { refs.current[name] = node }} id={`office-tab-${name}`} role="tab"
            type="button" aria-selected={tab === name} aria-controls={`office-panel-${name}`} tabIndex={tab === name ? 0 : -1}
            onClick={() => setTab(name)} onKeyDown={(event) => onKeyDown(event, name)}
            className={`min-h-11 whitespace-nowrap px-4 text-sm ${tab === name ? 'border-b-2 border-acento text-acento-texto' : 'text-texto-suave hover:text-texto'}`}
          >{t.virtualOffice.tabs[name]}</button>)}
        </div>
        <div className="rounded-xl border border-borde bg-superficie p-5"><Panel tab={tab} t={t} /></div>
      </section>
      {showAccess && <Dialogo idTitulo="office-access-info" titulo={t.virtualOffice.accessTitle} onCerrar={() => setShowAccess(false)}>
        <p className="text-sm text-texto-suave">{t.virtualOffice.accessBody}</p>
        <div className="mt-5 flex justify-end"><button type="button" onClick={() => setShowAccess(false)} className={`${TAMANO_BOTON_ACCION} rounded bg-accion text-sobre-color`}>{t.virtualOffice.close}</button></div>
      </Dialogo>}
    </main>
  )
}
