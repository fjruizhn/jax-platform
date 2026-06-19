export function createWebSocket(userId, token, onMessage, onStatusChange) {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const host = window.location.hostname
  const port = import.meta.env.DEV ? '8080' : window.location.port
  const url = `${protocol}://${host}:${port}/ws/${userId}?token=${token}`

  let ws = null
  let reconnectTimer = null
  let shouldStop = false
  let attempt = 0

  function connect() {
    ws = new WebSocket(url)

    ws.onopen = () => {
      attempt = 0
      clearTimeout(reconnectTimer)
      onStatusChange?.('connected')
    }

    ws.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data)
        onMessage(event)
      } catch {}
    }

    ws.onerror = () => {
      // onclose fires immediately after — status handled there
    }

    ws.onclose = () => {
      if (shouldStop) {
        onStatusChange?.('disconnected')
        return
      }
      const delay = Math.min(1000 * Math.pow(2, attempt), 30000)
      attempt++
      onStatusChange?.('reconnecting')
      reconnectTimer = setTimeout(connect, delay)
    }
  }

  connect()

  return {
    close: () => {
      shouldStop = true
      clearTimeout(reconnectTimer)
      ws?.close()
    },
    send: (data) => ws?.readyState === WebSocket.OPEN && ws.send(JSON.stringify(data)),
  }
}
