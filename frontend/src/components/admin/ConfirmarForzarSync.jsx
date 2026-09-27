import { useI18n } from '../../i18n/index.jsx'
import Dialogo from '../Dialogo'

// Confirmación de "forzar sincronización" (MAJOR-3(d), segunda auditoría
// adversarial del catálogo de modelos, 2026-09-27). Mismo criterio que
// ConfirmarCostoDialogo: consentimiento humano explícito, NO una acción
// destructiva (no borra nada, sólo salta un chequeo y queda auditado del
// lado del backend) -- por eso sin ConfirmacionSuma (esa es para lo
// irreversible). Va sobre Dialogo: portal, #root inert, foco, Escape =
// cancelar. Nunca window.confirm/alert/prompt.
export default function ConfirmarForzarSync({ proveedor, enviando, onConfirmar, onCancelar }) {
  const { t } = useI18n()
  return (
    <Dialogo idTitulo="confirmar-forzar-sync-titulo" titulo={t.adminModelsForzarTitulo}
      claseTitulo="text-sm font-semibold text-texto mb-2" onCerrar={onCancelar}
      cerrable={!enviando}>
      <p className="text-sm text-texto-suave mb-4 break-words">
        {t.adminModelsForzarMensaje(proveedor)}
      </p>
      <div className="flex gap-2 justify-end pt-2">
        <button type="button" onClick={onCancelar} disabled={enviando}
          className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto disabled:opacity-50 transition-colors">
          {t.cancel}
        </button>
        <button type="button" onClick={onConfirmar} disabled={enviando}
          className="px-4 py-1.5 rounded-lg bg-peligro-solido hover:bg-peligro-solido-hover text-sobre-color text-sm font-semibold disabled:opacity-50 transition-colors">
          {t.adminModelsForzarConfirmar}
        </button>
      </div>
    </Dialogo>
  )
}
