import { render, screen, fireEvent } from '@testing-library/react'
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

// Fix round 1 (2026-09-15, Ruling U24): Escape cierra los tres modales de
// usuarios. Nunca un clic en el fondo -- así no se pierde lo escrito.
describe('HistorialUsuario -- Escape (Ruling U24)', () => {
  it('Escape cierra el modal', () => {
    api.get.mockReturnValue(new Promise(() => {}))
    const onCerrar = vi.fn()
    render(<I18nProvider><HistorialUsuario usuario={{ user_id: 2, email: 'b@x.io' }} onCerrar={onCerrar} /></I18nProvider>)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onCerrar).toHaveBeenCalledTimes(1)
  })
})

// Ruling U27 (review final, 2026-09-15): usa Dialogo. Sin campos, el foco va
// al título; el panel conserva su ancho (max-w-lg).
describe('HistorialUsuario -- diálogo (Ruling U27)', () => {
  it('es un diálogo nombrado por su título, fuera de #root, con #root inert y el foco en el título', () => {
    api.get.mockReturnValue(new Promise(() => {}))
    const root = document.createElement('div')
    root.id = 'root'
    document.body.appendChild(root)
    try {
      render(vista({ user_id: 2, email: 'b@x.io' }), { container: root })
      const dialogo = screen.getByRole('dialog', { name: 'Historial de b@x.io' })
      expect(root.contains(dialogo)).toBe(false)
      expect(root).toHaveAttribute('inert')
      expect(screen.getByText('Historial de b@x.io')).toHaveFocus()
      expect(dialogo.className).toMatch(/(^|\s)max-w-lg(\s|$)/)
    } finally {
      root.remove()
    }
  })
})
