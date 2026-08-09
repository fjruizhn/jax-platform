import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  default: {
    post: vi.fn(),
    get: vi.fn(),
  },
}))

import api from '../api/client'
import { useJaxStore } from './useJaxStore'

const INITIAL_STATE = useJaxStore.getState()

describe('async writers do not resurrect a logged-out session', () => {
  beforeEach(() => {
    useJaxStore.setState({ ...INITIAL_STATE, token: 'test-token' }, true)
    vi.clearAllMocks()
  })

  it('does not apply a command_completed result if the session logged out mid-fetch', async () => {
    useJaxStore.setState({
      messages: [{ id: 'cmd-t1', facet: 'hyde', content: '_verificando…_', status: 'running', timestamp: 't0' }],
    })
    api.get.mockResolvedValue({ data: { result: 'resultado final' } })

    useJaxStore.getState().handleEvent({
      event_type: 'command_completed',
      payload: { task_id: 't1', result: null, result_preview: 'preview', status: 'completed' },
    })

    // logout mientras el fetch de /command/t1 está en vuelo
    useJaxStore.setState({ token: null })
    await new Promise((resolve) => setTimeout(resolve, 0))

    const msg = useJaxStore.getState().messages.find((m) => m.id === 'cmd-t1')
    expect(msg.status).toBe('running')
    expect(msg.content).toBe('_verificando…_')
  })

  it('command_completed does not touch an existing message when the session is already logged out', async () => {
    useJaxStore.setState({
      token: null,
      messages: [{ id: 'cmd-t1', facet: 'hyde', content: '_verificando…_', status: 'running', timestamp: 't0' }],
    })
    api.get.mockResolvedValue({ data: { result: 'no debería aplicarse' } })

    useJaxStore.getState().handleEvent({
      event_type: 'command_completed',
      payload: { task_id: 't1', result: 'resultado directo', result_preview: null, status: 'completed' },
    })
    await new Promise((resolve) => setTimeout(resolve, 0))

    const msg = useJaxStore.getState().messages.find((m) => m.id === 'cmd-t1')
    expect(msg.status).toBe('running')
    expect(msg.content).toBe('_verificando…_')
  })

  it('checkPendingTasks stops and does not write results once the session logs out mid-loop', async () => {
    useJaxStore.setState({
      messages: [
        { id: 'cmd-t1', status: 'running', timestamp: 't0' },
        { id: 'cmd-t2', status: 'running', timestamp: 't0' },
      ],
    })

    let resolveFirst
    api.get.mockImplementation((url) => {
      if (url === '/command/t1') {
        return new Promise((resolve) => { resolveFirst = resolve })
      }
      return Promise.resolve({ data: { status: 'completed', result: 'r2' } })
    })

    const pending = useJaxStore.getState().checkPendingTasks()

    // logout mientras el fetch de la primera tarea sigue en vuelo
    useJaxStore.setState({ token: null })
    resolveFirst({ data: { status: 'completed', result: 'r1' } })
    await pending

    expect(api.get).toHaveBeenCalledTimes(1) // nunca llega a pedir cmd-t2
    const messages = useJaxStore.getState().messages
    expect(messages.find((m) => m.id === 'cmd-t1').status).toBe('running')
    expect(messages.find((m) => m.id === 'cmd-t2').status).toBe('running')
  })

  it('the 5s-later rescheduled poll re-validates the session and does not fetch if logged out by then', async () => {
    vi.useFakeTimers()
    try {
      useJaxStore.setState({
        messages: [{ id: 'cmd-t1', status: 'running', timestamp: 't0' }],
      })
      api.get.mockResolvedValue({ data: { status: 'running' } })

      // primera pasada completa con sesión válida: sigue 'running' -> programa el reintento de 5s
      await useJaxStore.getState().checkPendingTasks()
      expect(api.get).toHaveBeenCalledTimes(1)

      // logout antes de que dispare el setTimeout del reintento
      useJaxStore.setState({ token: null })
      await vi.advanceTimersByTimeAsync(5000)

      // la llamada reprogramada capturó y validó su propio epoch al entrar
      // (no heredó el de la corrida anterior) -> no hace un segundo fetch
      expect(api.get).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('logout() does not leak session state into the next login', () => {
  beforeEach(() => {
    useJaxStore.setState({ ...INITIAL_STATE, token: 'test-token', user: { user_id: 1 } }, true)
    vi.clearAllMocks()
    api.post.mockResolvedValue({})
  })

  afterEach(() => {
    localStorage.removeItem('jax_pending_cmds')
  })

  it('a different user logging in on the same browser does not inherit the previous user\'s pending commands', () => {
    // user 1 tiene un comando pendiente
    useJaxStore.getState().registerPendingCommand('task-from-user-1')
    useJaxStore.getState().logout()

    // user 2 se loguea en el mismo browser (sin pasar por login() real —
    // sólo lo que le importa a jax_pending_cmds: token + user)
    useJaxStore.setState({ token: 'token-user-2', user: { user_id: 2 } })

    useJaxStore.getState().restorePendingTasks()

    // no debe aparecer ningún placeholder para el task de user 1
    expect(useJaxStore.getState().messages).toHaveLength(0)
  })

  it('the SAME user logging back in still sees their own pending commands (owner-scoping is not a blanket wipe)', () => {
    useJaxStore.getState().registerPendingCommand('task-from-user-1')
    useJaxStore.getState().logout()

    // user 1 vuelve a loguearse
    useJaxStore.setState({ token: 'token-user-1-again', user: { user_id: 1 } })

    useJaxStore.getState().restorePendingTasks()

    expect(useJaxStore.getState().messages.some((m) => m.id === 'cmd-task-from-user-1')).toBe(true)
  })

  it('bumps the session epoch so a stale pipeline-results retry does not write into a session that logs back in', async () => {
    vi.useFakeTimers()
    try {
      api.get.mockRejectedValue(new Error('network down'))

      useJaxStore.getState().handleEvent({
        event_type: 'pipeline_step_changed',
        payload: { pipeline_id: 'pid-leak', status: 'completed' },
      })
      // deja que el primer intento (rechazado) programe el reintento de 2s
      await vi.advanceTimersByTimeAsync(0)
      expect(api.get).toHaveBeenCalledTimes(1)

      // logout + un nuevo login rápido en el mismo browser, antes de que
      // dispare el reintento — el token vuelve a ser truthy, así que un
      // simple "¿hay token?" no alcanzaría para distinguir esto de la
      // misma sesión.
      useJaxStore.getState().logout()
      useJaxStore.setState((s) => ({
        token: 'new-session-token',
        user: { user_id: 2 },
        _sessionEpoch: s._sessionEpoch + 1,
      }))

      await vi.advanceTimersByTimeAsync(2000)

      // el reintento capturó el epoch de la sesión vieja -> ni siquiera
      // vuelve a llamar a la API, y no escribe nada en la sesión nueva
      expect(api.get).toHaveBeenCalledTimes(1)
      expect(useJaxStore.getState().messages).toHaveLength(0)
      expect(useJaxStore.getState().toasts.some((t) => t.type === 'error')).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })
})
