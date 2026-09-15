import { useEffect, useRef } from 'react'

// Escape cierra los modales de usuarios (Ruling U24, 2026-09-15). Desde el
// Ruling U27 lo usa sólo components/Dialogo.jsx, el diálogo único sobre el que
// van MiCuentaModal, EditarUsuarioModal, HistorialUsuario y CrearUsuarioModal.
// A propósito NO cierra con un clic en el backdrop -- un clic accidental fuera
// del modal no debe perder lo escrito.
//
// Fix round 2 (Ruling U25, 2026-09-15): `onCerrar` vive en un ref, actualizado
// en cada render, y el listener de `keydown` se agrega UNA sola vez (efecto
// con deps `[]`) y se quita al desmontar. Antes, `onCerrar` iba en las deps
// del efecto: como en la mayoría de los llamadores es un inline
// `() => setX(null)` (una función nueva en cada render), el listener se sacaba
// y se volvía a poner en cada render. El comportamiento no cambia: Escape
// sigue llamando siempre al `onCerrar` más reciente.
export function useCerrarConEscape(onCerrar) {
  const onCerrarRef = useRef(onCerrar)
  useEffect(() => {
    onCerrarRef.current = onCerrar
  })

  useEffect(() => {
    function onKeyDown(e) {
      // `null` = diálogo no cerrable (U34, cambio obligatorio de contraseña).
      if (e.key === 'Escape') onCerrarRef.current?.()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [])
}
