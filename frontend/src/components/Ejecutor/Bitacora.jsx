import { useI18n, localeFor } from '../../i18n/index.jsx'
import { datosDeBitacora, traducir } from './textos'

// Bitácora de la misión: eventos en el orden en que llegan, con el código
// traducido (uno desconocido va crudo), turno, datos y hora en el locale activo.
export default function Bitacora({ eventos }) {
  const { t, lang } = useI18n()
  const tx = t.ejecutor
  return (
    <section aria-labelledby="ejecutor-bitacora" className="rounded-lg border border-borde bg-superficie p-3">
      <h3 id="ejecutor-bitacora" className="text-xs font-semibold text-texto-fuerte mb-2">{tx.bitacora}</h3>
      {eventos.length === 0 ? (
        <p className="text-xs text-texto-suave">{tx.sinEventos}</p>
      ) : (
        <ol aria-label={tx.bitacora} className="space-y-1">
          {eventos.map((e) => (
            <li key={e.id} className="text-xs text-texto flex flex-wrap gap-x-2">
              <span className="text-texto-tenue">{e.at ? new Date(e.at).toLocaleString(localeFor(lang)) : ''}</span>
              {e.turno !== null && e.turno !== undefined && <span className="text-texto-suave">{tx.turno(e.turno)}</span>}
              <span className="font-semibold">{traducir(tx.eventos, e.evento)}</span>
              {datosDeBitacora(tx, e.datos) && <span className="font-mono text-texto-suave break-all">{datosDeBitacora(tx, e.datos)}</span>}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
