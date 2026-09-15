import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Task 4, ronda 1 (2026-09-15): el efecto del historial ignora una respuesta
// que llega después de cerrar el modal o de cambiar de usuario (antes la
// aplicaba igual: toast de un error de un modal ya cerrado, o la lista de
// otro usuario pisando la actual).
const addToastMock = vi.fn()
vi.mock('../../store/useJaxStore', () => ({
  useJaxStore: (selector) => selector({ addToast: addToastMock }),
}))
vi.mock('../../api/client', () => ({ default: { get: vi.fn() } }))

import api from '../../api/client'
import HistorialUsuario from './HistorialUsuario'
import { I18nProvider } from '../../i18n/index.jsx'

function entrada(id, action) {
  return { id, ts: null, actor_user_id: 1, actor_email: 'f@x.io', action, detail: null, ip: null }
}

function vista(usuario) {
  return <I18nProvider><HistorialUsuario usuario={usuario} onCerrar={vi.fn()} /></I18nProvider>
}

const tic = () => new Promise((r) => setTimeout(r, 0))

beforeEach(() => {
  addToastMock.mockReset()
  api.get.mockReset()
  localStorage.clear()
})

describe('HistorialUsuario -- respuestas tardías', () => {
  it('si se cierra antes de que llegue la respuesta, un error tardío no dispara el toast', async () => {
    let rechazar
    api.get.mockReturnValue(new Promise((_, r) => { rechazar = r }))
    const { unmount } = render(vista({ user_id: 2, email: 'b@x.io' }))
    unmount()
    rechazar({ response: { status: 500, data: {} } })
    await tic()
    expect(addToastMock).not.toHaveBeenCalled()
  })

  it('al cambiar de usuario, la respuesta vieja no pisa la nueva', async () => {
    const pendientes = {}
    api.get.mockImplementation((url) => new Promise((res) => { pendientes[url] = res }))
    const { rerender } = render(vista({ user_id: 2, email: 'b@x.io' }))
    rerender(vista({ user_id: 3, email: 'c@x.io' }))
    pendientes['/admin/users/3/audit']({ data: { entries: [entrada(2, 'unlock')] } })
    expect(await screen.findByText('Desbloqueo')).toBeInTheDocument()
    pendientes['/admin/users/2/audit']({ data: { entries: [entrada(1, 'baja')] } })
    await tic()
    expect(screen.queryByText('Baja')).not.toBeInTheDocument()
    expect(screen.getByText('Desbloqueo')).toBeInTheDocument()
  })
})
