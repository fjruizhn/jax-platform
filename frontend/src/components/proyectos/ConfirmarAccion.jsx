import { useState, useRef } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import Dialogo from '../Dialogo'
import { TAMANO_BOTON_44 } from '../../tema/botones'

// Confirmación de una acción de proyecto sobre Dialogo (nunca confirm()).
// `onConfirmar` hace la llamada; este componente solo evita el doble envío.
const FOCO = 'focus:outline-none focus-visible:ring-2 focus-visible:ring-foco'
const SECUNDARIO = `${TAMANO_BOTON_44} rounded bg-superficie text-texto-suave hover:text-texto ${FOCO} transition-colors`
const PELIGRO = `${TAMANO_BOTON_44} rounded bg-peligro-solido hover:bg-peligro-solido-hover text-sobre-color ${FOCO} disabled:opacity-50 transition-colors`

export default function ConfirmarAccion({ titulo, mensaje, textoConfirmar, onConfirmar, onCancelar }) {
  const { t } = useI18n()
  const [enviando, setEnviando] = useState(false)
  const enviandoRef = useRef(false)

  async function confirmar() {
    if (enviandoRef.current) return
    enviandoRef.current = true
    setEnviando(true)
    try {
      await onConfirmar()
    } finally {
      enviandoRef.current = false
      setEnviando(false)
    }
  }

  return (
    <Dialogo idTitulo="confirmar-accion-titulo" titulo={titulo} onCerrar={onCancelar}>
      <p className="text-sm text-texto-suave mb-4 break-words">{mensaje}</p>
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onCancelar} className={SECUNDARIO}>{t.proyectos.cancelar}</button>
        <button type="button" onClick={confirmar} disabled={enviando} className={PELIGRO}>{textoConfirmar}</button>
      </div>
    </Dialogo>
  )
}
