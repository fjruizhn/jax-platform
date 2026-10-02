import { render, screen, fireEvent, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

vi.mock('../../api/client', () => ({
  default: { get: vi.fn(() => Promise.resolve({ data: {} })), post: vi.fn() },
}))
vi.mock('../../api/proyectos', () => ({
  listarProyectos: vi.fn(() => Promise.resolve({ proyectos: [{ id: 7, nombre: 'Siete' }] })),
}))

import BottomBar from './BottomBar'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import es from '../../i18n/es.js'

const INICIAL = useJaxStore.getState()

function renderBar(rol = 'operator') {
  useJaxStore.setState({ ...INICIAL, user: { role: rol }, proyectoActivo: null, toasts: [] }, true)
  return render(<I18nProvider><BottomBar /></I18nProvider>)
}

beforeEach(() => localStorage.clear())

// E1.1 (Fernando, 2026-10-02): el selector de proyecto es una etiqueta chica
// pegada a Chat, DENTRO de la fila de modos.
describe('BottomBar -- fila de modos con el selector de proyecto', () => {
  it('orden: Chat, selector, Ejecutor (superadmin), Pipeline, Imagen', () => {
    renderBar('superadmin')
    const fila = screen.getByTestId('fila-modos')
    const items = Array.from(fila.querySelectorAll('button, select'))
    const nombres = items.map((el) => (el.tagName === 'SELECT' ? 'SELECT' : el.textContent))
    expect(nombres).toEqual([es.modeChat, 'SELECT', es.ejecutor.modo, es.modePipeline, es.modeImagen])
  })

  it('sin superadmin no hay Ejecutor', () => {
    renderBar('operator')
    const fila = screen.getByTestId('fila-modos')
    const nombres = Array.from(fila.querySelectorAll('button, select')).map((el) => (el.tagName === 'SELECT' ? 'SELECT' : el.textContent))
    expect(nombres).toEqual([es.modeChat, 'SELECT', es.modePipeline, es.modeImagen])
  })

  it('el selector vive dentro de la fila, con etiqueta i18n, y se ve como los botones vecinos', () => {
    renderBar()
    const fila = screen.getByTestId('fila-modos')
    const sel = within(fila).getByLabelText(es.proyectos.selectorChat.etiqueta)
    expect(sel.tagName).toBe('SELECT')
    const chat = screen.getByRole('button', { name: es.modeChat })
    for (const c of ['px-2', 'text-xs', 'font-semibold', 'rounded']) {
      expect(sel.className).toContain(c)
      expect(chat.className).toContain(c)
    }
    expect(sel.className).not.toContain('min-h-11')
    expect(sel.className).toContain('focus-visible:ring-2')
  })

  it('solo se ve en modo Chat: desaparece al cambiar de modo y vuelve', () => {
    renderBar()
    expect(screen.getByLabelText(es.proyectos.selectorChat.etiqueta)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: es.modePipeline }))
    expect(screen.queryByLabelText(es.proyectos.selectorChat.etiqueta)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: es.modeImagen }))
    expect(screen.queryByLabelText(es.proyectos.selectorChat.etiqueta)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: es.modeChat }))
    expect(screen.getByLabelText(es.proyectos.selectorChat.etiqueta)).toBeInTheDocument()
  })
})
