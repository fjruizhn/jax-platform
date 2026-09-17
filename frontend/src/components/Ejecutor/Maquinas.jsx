import { useI18n } from '../../i18n/index.jsx'
import { useEjecutor } from '../../store/useEjecutor'
import { traducir } from './textos'

// Todas las máquinas de /estado. Las no elegibles se MUESTRAN deshabilitadas
// con su motivo: la compuerta de datos de clientes cerrada se ve, no se esconde.
export default function Maquinas({ estado }) {
  const { t } = useI18n()
  const tx = t.ejecutor
  const seleccion = useEjecutor((s) => s.seleccion)
  const alternar = useEjecutor((s) => s.alternarMaquina)
  const maquinas = estado.maquinas || []
  const hayElegibles = maquinas.some((m) => m.elegible)

  return (
    <section aria-labelledby="ejecutor-maquinas" className="rounded-lg border border-borde bg-superficie p-3">
      <h3 id="ejecutor-maquinas" className="text-xs font-semibold text-texto-fuerte mb-2">{tx.maquinasTitulo}</h3>
      <p className="text-xs text-texto-suave mb-2">
        <span>{tx.compuertaRotulo}</span>
        {': '}
        <span className="font-semibold text-texto">{traducir(tx.compuertaEstados, estado.compuerta_datos_de_clientes)}</span>
      </p>
      {!hayElegibles && <p className="text-xs text-aviso mb-2">{tx.sinElegibles}</p>}
      <ul className="space-y-1">
        {maquinas.map((m) => {
          const id = `ejecutor-maquina-${m.nombre}`
          return (
            <li key={m.nombre} className="flex items-center gap-2 text-xs">
              <input
                id={id}
                type="checkbox"
                disabled={!m.elegible}
                checked={m.elegible && seleccion.includes(m.nombre)}
                onChange={() => alternar(m.nombre)}
                className="disabled:opacity-50"
              />
              <label htmlFor={id} className={m.elegible ? 'text-texto' : 'text-texto-tenue'}>
                <span className="font-mono">{m.nombre}</span>
                {m.rol && <span className="text-texto-suave"> · {m.rol}</span>}
              </label>
              {!m.elegible && (
                <span className="text-aviso">{traducir(tx.motivosNoElegible, m.motivo_no_elegible)}</span>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
