import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

vi.mock('../../api/proyectos', () => ({ listarProyectos: vi.fn() }))

import { listarProyectos } from '../../api/proyectos'
import SelectorDeProyecto from './SelectorDeProyecto'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import es from '../../i18n/es.js'

const INICIAL = useJaxStore.getState()
const A = { id: 1, nombre: 'Alfa' }
const B = { id: 2, nombre: 'Beta' }

function renderSel() {
  return render(<I18nProvider><SelectorDeProyecto /></I18nProvider>)
}

beforeEach(() => {
  localStorage.clear()
  useJaxStore.setState({ ...INICIAL, proyectoActivo: null, toasts: [] }, true)
  listarProyectos.mockReset()
})

describe('SelectorDeProyecto', () => {
  it('lista Personal + los proyectos activos, con etiqueta i18n y el tamaño de los botones de modo', async () => {
    listarProyectos.mockResolvedValue({ proyectos: [A, B] })
    renderSel()
    const sel = screen.getByLabelText(es.proyectos.selectorChat.etiqueta)
    await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(3))
    expect(screen.getByRole('option', { name: es.proyectos.selectorChat.personal })).toBeInTheDocument()
    expect(sel.className).toContain('min-h-6')
    expect(listarProyectos).toHaveBeenCalledWith({ vista: 'activos', limite: 100 })
  })

  it('elegir un proyecto lo deja en el store; volver a Personal lo quita', async () => {
    listarProyectos.mockResolvedValue({ proyectos: [A, B] })
    renderSel()
    await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(3))
    const sel = screen.getByLabelText(es.proyectos.selectorChat.etiqueta)
    fireEvent.change(sel, { target: { value: '2' } })
    expect(useJaxStore.getState().proyectoActivo).toEqual({ id: 2, nombre: 'Beta' })
    fireEvent.change(sel, { target: { value: '' } })
    expect(useJaxStore.getState().proyectoActivo).toBeNull()
  })

  it('al abrirlo recarga la lista', async () => {
    listarProyectos.mockResolvedValue({ proyectos: [A] })
    renderSel()
    await waitFor(() => expect(listarProyectos).toHaveBeenCalledTimes(1))
    fireEvent.mouseDown(screen.getByLabelText(es.proyectos.selectorChat.etiqueta))
    await waitFor(() => expect(listarProyectos).toHaveBeenCalledTimes(2))
  })

  it('un proyecto que desaparece de la lista se des-elige con aviso', async () => {
    useJaxStore.setState({ proyectoActivo: A })
    listarProyectos.mockResolvedValueOnce({ proyectos: [A] }).mockResolvedValueOnce({ proyectos: [B] })
    renderSel()
    await waitFor(() => expect(listarProyectos).toHaveBeenCalledTimes(1))
    expect(useJaxStore.getState().proyectoActivo).toEqual(A)
    fireEvent.mouseDown(screen.getByLabelText(es.proyectos.selectorChat.etiqueta))
    await waitFor(() => expect(useJaxStore.getState().proyectoActivo).toBeNull())
    expect(useJaxStore.getState().toasts.map((x) => x.message)).toContain(es.proyectos.proyectoNoDisponible)
  })

  it('al montar revalida el proyecto activo contra la lista', async () => {
    useJaxStore.setState({ proyectoActivo: A })
    listarProyectos.mockResolvedValue({ proyectos: [B] })
    renderSel()
    await waitFor(() => expect(useJaxStore.getState().proyectoActivo).toBeNull())
    expect(useJaxStore.getState().toasts.map((x) => x.message)).toContain(es.proyectos.proyectoNoDisponible)
  })

  it('si la lista falla no des-elige nada', async () => {
    useJaxStore.setState({ proyectoActivo: A })
    listarProyectos.mockRejectedValue(new Error('red'))
    renderSel()
    await waitFor(() => expect(listarProyectos).toHaveBeenCalled())
    expect(useJaxStore.getState().proyectoActivo).toEqual(A)
  })

  it('bloquea cambios de proyecto mientras hay un PDF escaneado ligado al proyecto activo', async () => {
    useJaxStore.setState({ proyectoActivo: A })
    listarProyectos.mockResolvedValue({ proyectos: [A, B] })
    render(<I18nProvider><SelectorDeProyecto bloqueado /></I18nProvider>)
    const selector = await screen.findByLabelText(es.proyectos.selectorChat.etiqueta)
    expect(selector).toBeDisabled()
    fireEvent.change(selector, { target: { value: '2' } })
    expect(useJaxStore.getState().proyectoActivo).toEqual(A)
  })
})
