import { useState } from 'react'
import { useI18n } from '../i18n/index.jsx'
import Dialogo from './Dialogo'

// Confirmación por suma (2026-09-12, admin usuarios etapa 5, spec §3.5).
// "Resolvé a + b = ?": el botón destructivo se habilita solo con la respuesta
// correcta. Reusable para todo borrado futuro; reemplaza a window.confirm.
// `numeros` existe para los tests (por defecto, al azar).
//
// Va sobre Dialogo (Ruling U28): portal, #root inert, foco al primer campo
// (la respuesta; por eso no hay autoFocus), Escape = cancelar, role/aria.
// Pinta solo con tokens. El botón destructivo usa el par declarado en PARES
// sobre-color / peligro-solido (y peligro-solido-hover).
//
// `mensaje` lleva `break-words` (M4, revisión adversarial de jax-platform PR
// 146, tercera vuelta): `Dialogo` fija un ancho acotado (`max-w-md` por
// defecto) -- un `mensaje` con una palabra larga y sin espacios (por
// ejemplo, `superviviente_texto` de la pantalla de Memoria, que puede venir
// sin puntuación) desbordaba el panel en vez de partirse. Es aditivo: no
// cambia nada para los `mensaje` cortos y con espacios que ya usan las
// otras pantallas (AdminUsers, AdminRepository, KillSwitch, ...).
export function numerosAlAzar(aleatorio = Math.random) {
  return [10 + Math.floor(aleatorio() * 40), 1 + Math.floor(aleatorio() * 9)]
}

const CAMPO = 'w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco'

export default function ConfirmacionSuma({ titulo, mensaje, textoConfirmar, onConfirmar, onCancelar, numeros }) {
  const { t } = useI18n()
  const [[a, b]] = useState(() => numeros || numerosAlAzar())
  const [respuesta, setRespuesta] = useState('')
  const [enviando, setEnviando] = useState(false)
  const correcta = respuesta.trim() !== '' && Number(respuesta) === a + b

  async function confirmar(e) {
    e.preventDefault()
    if (!correcta || enviando) return
    setEnviando(true)
    try {
      await onConfirmar()
    } finally {
      setEnviando(false)
    }
  }

  return (
    <Dialogo idTitulo="confirmacion-suma-titulo" titulo={titulo} claseTitulo="text-sm font-semibold text-texto mb-2" onCerrar={onCancelar}>
      <p className="text-sm text-texto-suave mb-4 break-words">{mensaje}</p>
      <form onSubmit={confirmar} className="space-y-3">
        <label htmlFor="confirmacion-suma-respuesta" className="block text-sm text-texto">{t.confirmSumLabel(a, b)}</label>
        <input
          id="confirmacion-suma-respuesta"
          type="number"
          inputMode="numeric"
          value={respuesta}
          onChange={(e) => setRespuesta(e.target.value)}
          className={CAMPO}
        />
        <p className="text-xs text-texto-tenue">{t.confirmSumHint}</p>
        <div className="flex gap-2 justify-end pt-2">
          <button type="button" onClick={onCancelar} className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors">{t.adminCreateCancel}</button>
          <button type="submit" disabled={!correcta || enviando} className="px-4 py-1.5 rounded-lg bg-peligro-solido hover:bg-peligro-solido-hover text-sobre-color text-sm font-semibold disabled:opacity-50 transition-colors">
            {textoConfirmar}
          </button>
        </div>
      </form>
    </Dialogo>
  )
}
