import { useEffect, useRef } from 'react'
import { Navigate } from 'react-router-dom'
import { useJaxStore } from '../store/useJaxStore'
import MiCuentaModal from './MiCuentaModal'

// Puerta de la app (movida de App.jsx, 2026-09-15). Con must_change_password
// (el admin fijó la contraseña, Ruling U34) la app NO se monta: sólo el cambio
// obligatorio, no cerrable. Así Dashboard, su WebSocket y sus polls no piden
// nada que el backend negaría de todas formas (403 cambio_de_password_requerido,
// WS 4001).
//
// Foco tras el cambio: el diálogo se desmonta y Dialogo devolvería el foco a
// lo que lo tenía al abrir -- al entrar a la app, body. Cuando la marca pasa
// de prendida a apagada, el foco va al punto de entrada de la pantalla que se
// monta ([data-foco-inicial]: el campo del chat en "/", el <main> de Admin).
// El efecto de este componente corre después de los de sus hijos: la
// pantalla ya está montada.
export default function RequireAuth({ children }) {
  const token = useJaxStore((s) => s.token)
  const user = useJaxStore((s) => s.user)
  const sessionRestoring = useJaxStore((s) => s.sessionRestoring)
  const obligatorio = !!user?.must_change_password
  const veniaDelCambio = useRef(false)

  useEffect(() => {
    if (obligatorio) {
      veniaDelCambio.current = true
      return
    }
    if (!veniaDelCambio.current) return
    veniaDelCambio.current = false
    document.getElementById('root')?.querySelector('[data-foco-inicial]')?.focus()
  }, [obligatorio])

  if (sessionRestoring) return null
  if (!token) return <Navigate to="/login" replace />
  if (obligatorio) {
    return <div className="h-dvh bg-fondo"><MiCuentaModal obligatorio onCerrar={() => {}} /></div>
  }
  return children
}
