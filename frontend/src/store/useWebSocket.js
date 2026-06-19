import { useEffect, useRef } from 'react'
import { useJaxStore } from './useJaxStore'
import { createWebSocket } from '../api/websocket'

export function useWebSocket() {
  const { token, user, handleEvent, setWsStatus, loadState, checkPendingTasks, restorePendingTasks } = useJaxStore()
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
