export function createWebSocket(userId, token, onMessage, onStatusChange) {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const host = window.location.hostname
  const isLocalDevHost = host === 'localhost' || host === '127.0.0.1'
  const port = import.meta.env.DEV && isLocalDevHost
    ? ':8080'
    : (window.location.port ? `:${window.location.port}` : '')
  const url = `${protocol}://${host}${port}/ws/${userId}`

  let ws = null
  let reconnectTimer = null
  let shouldStop = false
  let attempt = 0
  let authenticated = false

  function connect() {
    authenticated = false
    ws = new WebSocket(url)

    ws.onopen = () => {
      try {
        ws.send(JSON.stringify({ type: 'auth', token }))
      } catch {
        ws?.close()
      }
    }

    ws.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data)

        if (event?.type === 'auth_ok') {
          authenticated = true
          attempt = 0
          clearTimeout(reconnectTimer)
          onStatusChange?.('connected')
          return
        }

        if (authenticated) {
          onMessage(event)
        }
      } catch {}
    }

    ws.onerror = () => {
      // onclose fires immediately after — status handled there
    }

    ws.onclose = (event) => {
      authenticated = false

      if (shouldStop) {
        onStatusChange?.('disconnected')
        return
      }

      if (event?.code === 4001) {
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
