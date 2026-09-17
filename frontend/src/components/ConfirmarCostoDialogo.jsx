import { useI18n } from '../i18n/index.jsx'
import Dialogo from './Dialogo'
import AlertaError from './AlertaError'
import { formatearUsd } from '../lib/moneda'
import { textoDeMotivoDeCosto } from '../api/errores'

// Confirmación de costo máximo (spec 2026-09-17 §6.2). Consentimiento humano,
// no una acción destructiva: sin suma (no es ConfirmacionSuma). Va sobre
// Dialogo (portal, inert, foco, Escape = cancelar). Quien la abre encima de
// otro Dialogo le pasa cerrable={false} mientras está abierta (desvío DV-12).
//
// Un paso sin costo acotado es el que no trae un monto legible (usd_max null
// o ilegible): se avisa con su motivo traducido, nunca con el código crudo.
// El lugar del paso ("Paso 5 (kimi)") sale del mismo helper de i18n que las
// violaciones (adenda Task 9). `aviso`: por qué se volvió a pedir (p. ej. el
// costo subió entre el pre-vuelo y la creación).
export default function ConfirmarCostoDialogo({ veredicto, enviando, aviso, onConfirmar, onCancelar }) {
  const { t, lang } = useI18n()
  const pasos = Array.isArray(veredicto.pasos_costo) ? veredicto.pasos_costo.filter((p) => p && typeof p === 'object') : []
  const montoDe = (p) => formatearUsd(p.usd_max, lang)
  const hayNoAcotados = pasos.some((p) => montoDe(p) === null)
  const umbral = formatearUsd(veredicto.umbral_usd, lang)
  const maximo = formatearUsd(veredicto.costo_max_usd, lang)
  return (
    <Dialogo idTitulo="confirmar-costo-titulo" titulo={t.confirmarCostoTitulo}
      claseTitulo="text-sm font-semibold text-texto mb-2" onCerrar={onCancelar}>
      {aviso && <AlertaError className="text-xs mb-2">{aviso}</AlertaError>}
      <p className="text-sm text-texto-suave mb-3">
        {t.confirmarCostoMensaje(maximo, umbral)}
      </p>
      <ul className="space-y-1 mb-3">
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
      <div className="flex gap-2 justify-end pt-2">
        <button type="button" onClick={onCancelar}
          className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors">
          {t.cancel}
        </button>
        <button type="button" onClick={onConfirmar} disabled={enviando}
          className="px-4 py-1.5 rounded-lg bg-accion hover:bg-accion-hover text-sobre-color text-sm font-semibold disabled:opacity-50 transition-colors">
          {t.confirmarCostoBoton}
        </button>
      </div>
    </Dialogo>
  )
}
