import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'

// Fix round 2 Task 10 (2026-09-17): los eventos que llegan mientras el WS está
// caído (pipeline_continued incluido) se pierden. El panel de detenidos se
// refresca por esos eventos, así que al RECONECTAR se vuelve a cargar el
// estado. La carga inicial ya corre al montar: la primera conexión no carga dos
// veces.
let alCambiarEstado = null
vi.mock('../api/websocket', () => ({
  createWebSocket: vi.fn((_uid, _token, _onEvent, onStatus) => {
    alCambiarEstado = onStatus
    return { close: vi.fn() }
  }),
}))

import { useWebSocket } from './useWebSocket'
import { useJaxStore } from './useJaxStore'

const loadState = vi.fn()
const checkPendingTasks = vi.fn()
const restorePendingTasks = vi.fn()

beforeEach(() => {
  alCambiarEstado = null
  for (const f of [loadState, checkPendingTasks, restorePendingTasks]) f.mockReset()
  useJaxStore.setState({
    token: 'tok', user: { user_id: 7, role: 'viewer' },
    loadState, checkPendingTasks, restorePendingTasks, setWsStatus: vi.fn(), handleEvent: vi.fn(),
  })
})

describe('useWebSocket -- recarga del estado al reconectar', () => {
  it('la primera conexión no vuelve a cargar el estado (ya se cargó al montar)', () => {
    renderHook(() => useWebSocket())
    expect(loadState).toHaveBeenCalledTimes(1)
    act(() => alCambiarEstado('connected'))
    expect(loadState).toHaveBeenCalledTimes(1)
    expect(restorePendingTasks).toHaveBeenCalledTimes(1)
  })

  it('una reconexión recarga el estado una vez y chequea las tareas', () => {
    renderHook(() => useWebSocket())
    act(() => alCambiarEstado('connected'))
    act(() => alCambiarEstado('disconnected'))
    expect(loadState).toHaveBeenCalledTimes(1)
    act(() => alCambiarEstado('connected'))
    expect(loadState).toHaveBeenCalledTimes(2)
    expect(checkPendingTasks).toHaveBeenCalledTimes(1)
  })
})
