import { useI18n, localeFor } from '../../i18n/index.jsx'
import { datosLegibles, traducir, traducirCodigo } from './textos'

// Detalle de una misión (SP2, 2026-09-17). Por turno: estado y código
// traducidos, rechazo, afirmaciones con la línea citada COMPLETA y LITERAL
// (whitespace-pre-wrap, sin recortar ni truncar), descartadas, salidas crudas
// SIEMPRE (también sin afirmaciones) y la verificación. Los rótulos son del
// frontend (i18n); los datos vienen tal cual del backend.
const MONO = 'font-mono text-xs text-texto bg-hundido border border-borde rounded px-2 py-1 whitespace-pre-wrap break-all'
const VERIFICACION = ['registro_cuadra', 'cadena_ok', 'pausa_puesta', 'auditor_pauso', 'auditor_legible']

function Campo({ rotulo, children }) {
  return (
    <div className="grid grid-cols-[7rem_1fr] gap-2 items-start">
      <dt className="text-xs text-texto-suave">{rotulo}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  )
}

function Turno({ turno }) {
  const { t, lang } = useI18n()
  const tx = t.ejecutor
  const fecha = (iso) => (iso ? new Date(iso).toLocaleString(localeFor(lang)) : '—')
  const afirmaciones = turno.afirmaciones || []
  const descartadas = turno.descartadas || []
  const crudas = turno.crudas || []
  const rechazo = turno.rechazo || []

  return (
    <article className="rounded-lg border border-borde bg-superficie p-3 space-y-3">
      <header className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-semibold text-texto-fuerte">{tx.turno(turno.n)}</span>
        <span className="px-1.5 py-0.5 rounded border border-borde text-texto">{traducir(tx.estadosTurno, turno.estado)}</span>
        {turno.codigo && <span className="text-peligro">{traducir(tx.codigosTurno, turno.codigo)}</span>}
        <span className="text-texto-suave">{tx.iniciado}: {fecha(turno.iniciado_at)}</span>
        <span className="text-texto-suave">{tx.terminado}: {fecha(turno.terminado_at)}</span>
      </header>
      {turno.instruccion && (
        <p className="text-xs text-texto"><span className="text-texto-suave">{tx.instruccion}: </span>{turno.instruccion}</p>
      )}

      {rechazo.length > 0 && (
        <div>
          <h5 className="text-xs font-semibold text-peligro mb-1">{tx.rechazo}</h5>
          <ul aria-label={tx.rechazo} className="space-y-1">
            {rechazo.map((r, i) => (
              <li key={i} className="text-xs text-texto">
                <span className="font-semibold">{traducir(tx.contratos, r.contrato)}</span>
                {' — '}
                <span>{traducirCodigo(tx, r.codigo)}</span>
                {datosLegibles(r.datos) && <span className="block font-mono text-texto-suave whitespace-pre-wrap break-all">{datosLegibles(r.datos)}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <h5 className="text-xs font-semibold text-texto-fuerte mb-1">{tx.afirmaciones}</h5>
        {afirmaciones.length === 0 ? (
          <p className="text-xs text-texto-suave">{tx.sinAfirmaciones}</p>
        ) : (
          <ul aria-label={tx.afirmaciones} className="space-y-2">
            {afirmaciones.map((a, i) => (
              <li key={i} className="border-l-2 border-exito-borde pl-2">
                <dl className="space-y-1">
                  {a.proposito && <Campo rotulo={tx.rotuloProposito}><span className="text-xs text-texto">{a.proposito}</span></Campo>}
                  <Campo rotulo={tx.rotuloDato}><span className="text-xs font-semibold text-texto-fuerte">{a.dato}</span></Campo>
                  <Campo rotulo={tx.rotuloMaquina}><span className="text-xs font-mono text-texto">{a.maquina}</span></Campo>
                  <Campo rotulo={tx.rotuloComando}><code className={`block ${MONO}`}>{a.comando}</code></Campo>
                  <Campo rotulo={tx.rotuloLinea}><pre data-linea-citada className={MONO}>{a.linea}</pre></Campo>
                </dl>
              </li>
            ))}
          </ul>
        )}
      </div>

      {descartadas.length > 0 && (
        <div>
          <h5 className="text-xs font-semibold text-aviso mb-1">{tx.descartadas}</h5>
          <ul aria-label={tx.descartadas} className="space-y-2">
            {descartadas.map((d, i) => (
              <li key={i} className="border-l-2 border-aviso-borde pl-2 space-y-1">
                <p className="text-xs">
                  <span className="text-aviso font-semibold">{traducirCodigo(tx, d.estado)}</span>
                  {d.codigo && <>{' — '}<span className="text-texto">{traducirCodigo(tx, d.codigo)}</span></>}
                </p>
                <dl className="space-y-1">
                  {d.dato && <Campo rotulo={tx.rotuloDato}><span className="text-xs text-texto">{d.dato}</span></Campo>}
                  {d.maquina && <Campo rotulo={tx.rotuloMaquina}><span className="text-xs font-mono text-texto">{d.maquina}</span></Campo>}
                  {d.comando && <Campo rotulo={tx.rotuloComando}><code className={`block ${MONO}`}>{d.comando}</code></Campo>}
                  {d.linea && <Campo rotulo={tx.rotuloLinea}><pre className={MONO}>{d.linea}</pre></Campo>}
                </dl>
                {datosLegibles(d.datos) && <p className="text-xs font-mono text-texto-suave whitespace-pre-wrap break-all">{datosLegibles(d.datos)}</p>}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <h5 className="text-xs font-semibold text-texto-fuerte mb-1">{tx.crudas}</h5>
        {crudas.length === 0 ? (
          <p className="text-xs text-texto-suave">{tx.sinCrudas}</p>
        ) : (
          <ul aria-label={tx.crudas} className="space-y-2">
            {crudas.map((c, i) => (
              <li key={i} className="space-y-1">
                <p className="text-xs text-texto-suave">
                  <span className="font-mono text-texto">{c.maquina}</span>
                  {' · '}
                  <code className="font-mono text-texto">{c.comando}</code>
                  {c.truncada && <span className="ml-2 px-1 rounded bg-aviso-fondo text-aviso font-semibold">{tx.truncada}</span>}
                </p>
                <pre data-salida-cruda className={`${MONO} max-h-96 overflow-y-auto`}>{c.salida}</pre>
              </li>
            ))}
          </ul>
        )}
      </div>

      {turno.verificacion && (
        <div>
          <h5 className="text-xs font-semibold text-texto-fuerte mb-1">{tx.verificacionTitulo}</h5>
          <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
            {VERIFICACION.map((clave) => (
              <li key={clave} className="text-texto-suave">
                <span>{tx.verificacion[clave]}</span>
                {': '}
                <span className="text-texto font-semibold">{turno.verificacion[clave] === true ? tx.si : tx.no}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </article>
  )
}

export default function DetalleMision({ mision }) {
  const { t } = useI18n()
  const tx = t.ejecutor
  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <p className="text-sm text-texto-fuerte font-semibold">{mision.objetivo}</p>
        <p className="text-xs text-texto-suave">
          <span data-estado-mision className="px-1.5 py-0.5 rounded border border-borde text-texto">{traducir(tx.estadosMision, mision.estado)}</span>
          {' · '}
          {tx.maquinasDeMision}: <span className="font-mono">{(mision.maquinas || []).join(', ')}</span>
        </p>
        <p className="text-xs text-texto-suave">{mision.puede_continuar ? tx.puedeContinuar : tx.noPuedeContinuar}</p>
      </div>
      {(mision.turnos || []).map((turno) => <Turno key={turno.n} turno={turno} />)}
    </div>
  )
}
