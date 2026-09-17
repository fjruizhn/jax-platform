import { useI18n } from '../i18n/index.jsx'
import { formatearUsd } from '../lib/moneda'
import { textoDeMotivoDeCosto } from '../api/errores'

// Costo máximo por paso de un veredicto del pre-vuelo (spec 2026-09-17 §6.2).
// Lo usan la confirmación de costo y la ventana de continuar (Task 10).
//
// Un paso sin costo acotado es el que no trae un monto legible (usd_max null
// o ilegible): se avisa con su motivo traducido, nunca con el código crudo.
// El lugar del paso ("Paso 5 (kimi)") sale del mismo helper de i18n que las
// violaciones (adenda Task 9).
export default function CostoPorPaso({ pasosCosto, className = 'space-y-1 mb-3' }) {
  const { t, lang } = useI18n()
  const pasos = Array.isArray(pasosCosto) ? pasosCosto.filter((p) => p && typeof p === 'object') : []
  const montoDe = (p) => formatearUsd(p.usd_max, lang)
  const hayNoAcotados = pasos.some((p) => montoDe(p) === null)
  return (
    <>
      <ul className={className}>
        {pasos.map((p, i) => {
          const monto = montoDe(p)
          return (
            <li key={`${p.paso}-${i}`} className="text-xs text-texto">
              {monto === null
                ? t.confirmarCostoPasoNoAcotado(p, textoDeMotivoDeCosto(t, p.motivo))
                : t.confirmarCostoPaso(p, monto)}
            </li>
          )
        })}
      </ul>
      {hayNoAcotados && <p className="text-xs text-aviso mb-3">{t.confirmarCostoNoAcotado}</p>}
    </>
  )
}
