import { useEffect, useRef } from 'react'
import { useJaxStore } from './useJaxStore'
import { createWebSocket } from '../api/websocket'

export function useWebSocket() {
  const token = useJaxStore((s) => s.token)
  const user = useJaxStore((s) => s.user)
  const handleEvent = useJaxStore((s) => s.handleEvent)
  const setWsStatus = useJaxStore((s) => s.setWsStatus)
  const loadState = useJaxStore((s) => s.loadState)
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
          // reconexión — recargar el estado: los eventos del corte (pipeline_continued,
          // pipeline_step_changed...) se perdieron, y el panel de detenidos se
          // refresca por ellos (fix round 2 Task 10). La primera conexión no:
          // loadState ya corrió al montar.
          loadState()
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
}
