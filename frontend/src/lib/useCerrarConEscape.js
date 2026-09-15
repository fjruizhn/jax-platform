import { useEffect } from 'react'

// Escape cierra los modales de usuarios (Ruling U24, 2026-09-15): MiCuentaModal,
// EditarUsuarioModal, HistorialUsuario. A propósito NO cierra con un clic en el
// backdrop -- un clic accidental fuera del modal no debe perder lo escrito.
export function useCerrarConEscape(onCerrar) {
  useEffect(() => {
    function onKeyDown(e) {
      if (e.key === 'Escape') onCerrar()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onCerrar])
}
