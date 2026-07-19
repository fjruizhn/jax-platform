import { useEffect, useRef } from 'react'
import { useJaxStore } from './useJaxStore'
import { createWebSocket } from '../api/websocket'

export function useWebSocket() {
  const token = useJaxStore((s) => s.token)
  const user = useJaxStore((s) => s.user)
  const handleEvent = useJaxStore((s) => s.handleEvent)
  const setWsStatus = useJaxStore((s) => s.setWsStatus)
  const loadState = useJaxStore((s) => s.loadState)
  const checkPendingTasks = useJaxStore((s) => s.checkPendingTasks)
  const restorePendingTasks = useJaxStore((s) => s.restorePendingTasks)
  const wsRef = useRef(null)
  const everConnectedRef = useRef(false)

  useEffect(() => {
    if (!token || !user) return

    loadState()
    everConnectedRef.current = false

    const handleStatus = (status) => {
      setWsStatus(status)
      if (status === 'connected') {
        if (everConnectedRef.current) {
          // reconexión — chequear tareas que completaron mientras el WS estaba caído
          checkPendingTasks()
        } else {
          // primera conexión — restaurar tareas pendientes de sesiones anteriores
          restorePendingTasks()
        }
        everConnectedRef.current = true
      }
    }

    wsRef.current = createWebSocket(
      String(user.user_id),
      token,
      handleEvent,
      handleStatus,
    )

    return () => {
      wsRef.current?.close()
    }
  }, [token, user?.user_id])

  return wsRef.current
}
