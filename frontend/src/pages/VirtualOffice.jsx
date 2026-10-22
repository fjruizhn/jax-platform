import { useState } from 'react'
import { useI18n } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
import Dialogo from '../components/Dialogo'
import { TAMANO_BOTON_ACCION } from '../tema/botones'

export default function VirtualOffice() {
  const { t } = useI18n()
  const user = useJaxStore((s) => s.user)
  const [showAccess, setShowAccess] = useState(false)

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
          <h2 className="text-base font-semibold">{t.virtualOffice.pendingTitle}</h2>
          <p role="status" className="mt-3 rounded-lg border border-borde bg-hundido p-3 text-sm text-texto-suave">{t.virtualOffice.pending}</p>
        </section>
      </section>
      {showAccess && <Dialogo idTitulo="office-access-info" titulo={t.virtualOffice.accessTitle} onCerrar={() => setShowAccess(false)}>
        <p className="text-sm text-texto-suave">{t.virtualOffice.accessBody}</p>
        <div className="mt-5 flex justify-end"><button type="button" onClick={() => setShowAccess(false)} className={`${TAMANO_BOTON_ACCION} rounded bg-accion text-sobre-color`}>{t.virtualOffice.close}</button></div>
      </Dialogo>}
    </main>
  )
}
