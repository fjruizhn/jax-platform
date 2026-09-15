import { useLayoutEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useCerrarConEscape } from '../lib/useCerrarConEscape'

// Diálogo modal único (Ruling U27, review final de la etapa 4, 2026-09-15).
// Antes cada modal hacía el diálogo a mano y a ninguno le salía completo: el
// fondo (sidebar de admin incluido) seguía alcanzable con Tab, el foco no
// entraba al diálogo ni volvía al disparador, y el alta ni siquiera era un
// diálogo. Este primitivo concentra el contrato:
//
// - Portal a document.body: el diálogo vive FUERA de #root, así que marcar
//   #root como `inert` apaga toda la app (sidebar, barra, tabla) sin apagar
//   el diálogo.
// - `inert` en #root mientras haya al menos un Dialogo abierto. El contador es
//   de módulo: cerrar uno no le quita el inert a otro que sigue abierto.
// - Al abrir, el foco va al primer campo; si no hay (Historial), al título
//   (tabIndex -1).
// - Al cerrar o desmontar se quita el inert PRIMERO y después se devuelve el
//   foco al elemento que lo tenía al abrir (si sigue en el documento): un
//   elemento dentro de un subárbol inert no puede tomar el foco.
// - Escape cierra (useCerrarConEscape). Un clic en el fondo NO: un clic
//   accidental no debe perder lo escrito (Ruling U24).
// - `cerrable={false}` (cambio obligatorio, U34): Escape no cierra; el modal
//   no dibuja botón de cerrar. Lo demás (portal, inert, foco, ARIA) no cambia.
// - role="dialog", aria-modal="true" y aria-labelledby al título.
//
// Colores: los mismos tokens que tenían los modales (velo bg-fondo/70, panel
// bg-superficie con borde). `className` lleva el ancho (max-w-md por defecto).
const CAMPOS = 'input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled])'

let abiertos = 0

function raiz() {
  return document.getElementById('root')
}

export default function Dialogo({ idTitulo, titulo, claseTitulo = 'text-sm font-semibold text-texto mb-4', onCerrar, cerrable = true, className = 'max-w-md', children }) {
  const panel = useRef(null)
  const tituloRef = useRef(null)
  useCerrarConEscape(cerrable ? onCerrar : null)

  // useLayoutEffect: el disparador todavía tiene el foco (nada pintó aún) y
  // el cleanup corre antes de que el navegador reciba otro evento.
  useLayoutEffect(() => {
    const previo = document.activeElement
    abiertos += 1
    raiz()?.setAttribute('inert', '')

    const campo = panel.current?.querySelector(CAMPOS)
    if (campo) campo.focus()
    else tituloRef.current?.focus()

    return () => {
      abiertos -= 1
      if (abiertos === 0) raiz()?.removeAttribute('inert')
      if (previo && previo !== document.body && previo.isConnected && typeof previo.focus === 'function') {
        previo.focus()
      }
    }
  }, [])

  return createPortal(
    <div className="fixed inset-0 bg-fondo/70 flex items-center justify-center z-50">
      <div ref={panel} role="dialog" aria-modal="true" aria-labelledby={idTitulo}
        className={`bg-superficie border border-borde rounded-xl p-6 w-full ${className} shadow-2xl`}>
        <h2 ref={tituloRef} id={idTitulo} tabIndex={-1} className={`${claseTitulo} focus:outline-none`}>{titulo}</h2>
        {children}
      </div>
    </div>,
    document.body,
  )
}
