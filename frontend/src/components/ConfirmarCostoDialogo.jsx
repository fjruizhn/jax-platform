import { useI18n } from '../i18n/index.jsx'
import Dialogo from './Dialogo'
import AlertaError from './AlertaError'
import CostoPorPaso from './CostoPorPaso'
import { formatearUsd } from '../lib/moneda'

// Confirmación de costo máximo (spec 2026-09-17 §6.2). Consentimiento humano,
// no una acción destructiva: sin suma (no es ConfirmacionSuma). Va sobre
// Dialogo (portal, inert, foco, Escape = cancelar). Quien la abre encima de
// otro Dialogo le pasa cerrable={false} mientras está abierta (desvío DV-12)
// y usa lib/useConfirmacionDeCosto.
// Mientras `enviando`, ni Cancelar ni Escape la cierran: la creación ya salió
// y se completaría igual (fix round 1 Task 9).
//
// El detalle por paso (y el aviso de pasos sin costo acotado) vive en
// CostoPorPaso, compartido con la ventana de continuar. `aviso`: por qué se
// volvió a pedir (p. ej. el costo subió entre el pre-vuelo y la creación).
export default function ConfirmarCostoDialogo({ veredicto, enviando, aviso, onConfirmar, onCancelar }) {
  const { t, lang } = useI18n()
  const umbral = formatearUsd(veredicto.umbral_usd, lang)
  const maximo = formatearUsd(veredicto.costo_max_usd, lang)
  return (
    <Dialogo idTitulo="confirmar-costo-titulo" titulo={t.confirmarCostoTitulo}
      claseTitulo="text-sm font-semibold text-texto mb-2" onCerrar={onCancelar}
      cerrable={!enviando}>
      {aviso && <AlertaError className="text-xs mb-2">{aviso}</AlertaError>}
      <p className="text-sm text-texto-suave mb-3">
        {t.confirmarCostoMensaje(maximo, umbral)}
      </p>
      <CostoPorPaso pasosCosto={veredicto.pasos_costo} />
      <div className="flex gap-2 justify-end pt-2">
        <button type="button" onClick={onCancelar} disabled={enviando}
          className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto disabled:opacity-50 transition-colors">
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
