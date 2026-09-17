import { useState } from 'react'
import { useI18n, localeFor } from '../../i18n/index.jsx'
import { useEjecutor } from '../../store/useEjecutor'
import ConfirmacionSuma from '../ConfirmacionSuma'
import { textoDeErrorEjecutor, traducir, traducirMotivoPausa } from './textos'

// Pausa del Ejecutor (NO el kill switch global). `legible: false` = el archivo
// existe pero no se pudo leer: se trata como PUESTA (fail-closed). Poner es
// inmediato (es la dirección segura); quitar pide la suma y muestra el motivo.
export default function PausaEjecutor({ pausa }) {
  const { t, lang } = useI18n()
  const tx = t.ejecutor
  const poner = useEjecutor((s) => s.ponerPausa)
  const quitar = useEjecutor((s) => s.quitarPausa)
  const [confirmando, setConfirmando] = useState(false)
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState(null)

  const ilegible = pausa?.legible === false
  const puesta = ilegible || pausa?.puesta === true
  const motivo = pausa?.motivo ? traducirMotivoPausa(tx, pausa.motivo) : tx.sinMotivo

  async function ejecutar(accion) {
    setError(null)
    setEnviando(true)
    try {
      await accion()
    } catch (err) {
      setError(textoDeErrorEjecutor(t, err))
    } finally {
      setEnviando(false)
      setConfirmando(false)
    }
  }

  return (
    <section aria-labelledby="ejecutor-pausa" className="rounded-lg border border-borde bg-superficie p-3">
      <h3 id="ejecutor-pausa" className="text-xs font-semibold text-texto-fuerte mb-2">{tx.pausaTitulo}</h3>
      {puesta ? (
        <div className="space-y-2">
          <p className="inline-flex items-center gap-2 px-2 py-1 rounded bg-peligro-fondo border border-peligro-borde text-peligro text-xs font-bold">
            <span className="w-2 h-2 rounded-full bg-peligro-solido" />
            {tx.pausaPuesta}
          </p>
          {ilegible && <p className="text-xs text-peligro">{tx.pausaIlegible}</p>}
          <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-xs">
            {pausa?.origen && (<><dt className="text-texto-suave">{tx.pausaOrigen}</dt><dd className="text-texto">{traducir(tx.origenes, pausa.origen)}</dd></>)}
            {pausa?.motivo && (<><dt className="text-texto-suave">{tx.pausaMotivo}</dt><dd className="text-texto">{motivo}</dd></>)}
            {pausa?.paso !== null && pausa?.paso !== undefined && (<><dt className="text-texto-suave">{tx.pausaPaso}</dt><dd className="text-texto">{String(pausa.paso)}</dd></>)}
            {pausa?.momento && (<><dt className="text-texto-suave">{tx.pausaMomento}</dt><dd className="text-texto">{new Date(pausa.momento).toLocaleString(localeFor(lang))}</dd></>)}
          </dl>
          <button
            type="button"
            disabled={enviando}
            onClick={() => { setError(null); setConfirmando(true) }}
            className="px-2 py-1 rounded bg-superficie-2 text-texto hover:text-texto-fuerte text-xs font-semibold disabled:opacity-50"
          >
            {tx.quitarPausa}
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-xs text-exito font-semibold">{tx.pausaNoPuesta}</p>
          <button
            type="button"
            disabled={enviando}
            onClick={() => ejecutar(poner)}
            className="px-3 py-1 rounded bg-peligro-fondo border border-peligro-borde hover:border-peligro-solido text-peligro text-xs font-semibold disabled:opacity-50"
          >
            {tx.pausar}
          </button>
          <p className="text-xs text-texto-suave">{tx.pausarAclaracion}</p>
        </div>
      )}
      {error && <p role="alert" className="mt-2 text-xs text-peligro">{error}</p>}
      {confirmando && (
        <ConfirmacionSuma
          titulo={tx.quitarPausaTitulo}
          mensaje={tx.quitarPausaMensaje(motivo)}
          textoConfirmar={tx.quitarPausaConfirmar}
          onConfirmar={() => ejecutar(quitar)}
          onCancelar={() => setConfirmando(false)}
        />
      )}
    </section>
  )
}
