import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

vi.mock('../../api/client', () => ({
  default: { get: vi.fn(() => Promise.resolve({ data: {} })), post: vi.fn() },
}))
vi.mock('../../api/proyectos', () => ({
  listarProyectos: vi.fn(() => Promise.resolve({ proyectos: [{ id: 7, nombre: 'Siete' }] })),
  verProyecto: vi.fn(() => Promise.resolve({ id: 7, nombre: 'Siete', estado: 'ACTIVE', papel: 'OWNER' })),
}))

import api from '../../api/client'
import BottomBar from './BottomBar'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import { useEjecutor } from '../../store/useEjecutor'
import es from '../../i18n/es.js'

const INICIAL = useJaxStore.getState()

function enviar(texto) {
  render(<I18nProvider><BottomBar /></I18nProvider>)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: texto } })
  fireEvent.click(screen.getByRole('button', { name: es.send }))
}

beforeEach(() => {
  localStorage.clear()
  useJaxStore.setState({ ...INICIAL, activeFacet: 'thot', messages: [], toasts: [], proyectoActivo: null }, true)
  api.post.mockReset()
  api.post.mockResolvedValue({ data: { facet: 'thot', response: 'ok', timestamp: 't' } })
})

describe('BottomBar -- project_id en /chat', () => {
  it('sin proyecto, el cuerpo NO lleva la clave project_id', async () => {
    enviar('hola')
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(api.post.mock.calls[0][1]).not.toHaveProperty('project_id')
  })

  it('con proyecto, el cuerpo lleva project_id numérico', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Siete' } })
    enviar('hola')
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(api.post.mock.calls[0][1].project_id).toBe(7)
  })

  it('403 project_scope_denied: vuelve a Personal, avisa, no reintenta y conserva el texto', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Siete' } })
    api.post.mockRejectedValue({ response: { status: 403, data: { detail: { code: 'project_scope_denied' } } } })
    enviar('mi mensaje')
    await waitFor(() => expect(useJaxStore.getState().proyectoActivo).toBeNull())
    expect(useJaxStore.getState().toasts.map((x) => x.message)).toContain(es.proyectos.proyectoNoDisponible)
    expect(api.post).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.getByRole('textbox')).toHaveValue('mi mensaje'))
    // reenviar a mano ya no lleva project_id
    api.post.mockResolvedValue({ data: { facet: 'thot', response: 'ok', timestamp: 't' } })
    fireEvent.click(screen.getByRole('button', { name: es.send }))
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2))
    expect(api.post.mock.calls[1][1]).not.toHaveProperty('project_id')
  })

  it('otro 403 no des-elige el proyecto', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Siete' } })
    api.post.mockRejectedValue({ response: { status: 403, data: { detail: { code: 'otra_cosa' } } } })
    enviar('hola')
    await waitFor(() => expect(useJaxStore.getState().messages).toHaveLength(2))
    expect(useJaxStore.getState().proyectoActivo).toEqual({ id: 7, nombre: 'Siete' })
  })
})

// E2a T11 (decisión C de Fernando): el selector de proyecto y el botón de
// documentos viven en Chat y Pipeline, con el tamaño de E1.1; no en Ejecutor ni
// en Imagen. El proyecto elegido es del store, así que cambiar de modo lo conserva.
describe('BottomBar -- selector de proyecto y botón de documentos por modo', () => {
  const ETIQUETA = es.proyectos.selectorChat.etiqueta
  const BOTON = es.proyectos.documentos.boton
  const pintar = () => render(<I18nProvider><BottomBar /></I18nProvider>)

  it('se ven en chat y en pipeline, y no en imagen', async () => {
    pintar()
    expect(screen.getByLabelText(ETIQUETA)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: BOTON })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: es.modePipeline }))
    expect(screen.getByLabelText(ETIQUETA)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: BOTON })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: es.modeImagen }))
    expect(screen.queryByLabelText(ETIQUETA)).toBeNull()
    expect(screen.queryByRole('button', { name: BOTON })).toBeNull()
  })

  it('no se ven en el modo Ejecutor', () => {
    useJaxStore.setState({ user: { user_id: '1', role: 'superadmin' } })
    useEjecutor.setState({ activo: true })
    try {
      pintar()
      expect(screen.queryByLabelText(ETIQUETA)).toBeNull()
      expect(screen.queryByRole('button', { name: BOTON })).toBeNull()
    } finally {
      useEjecutor.getState().reiniciar()
    }
  })

  it('en pipeline el selector y el botón siguen pegados a Chat, con el mismo tamaño de E1.1', () => {
    pintar()
    fireEvent.click(screen.getByRole('button', { name: es.modePipeline }))
    const fila = screen.getByTestId('fila-modos')
    const hijos = [...fila.children].map((n) => n.textContent)
    expect(hijos[0]).toBe(es.modeChat)
    expect(fila.contains(screen.getByLabelText(ETIQUETA))).toBe(true)
    expect(screen.getByLabelText(ETIQUETA).className).toContain('min-h-6')
    expect(screen.getByRole('button', { name: BOTON }).className).toContain('min-h-6')
  })

  it('cambiar de modo conserva el proyecto elegido', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Siete' } })
    pintar()
    await waitFor(() => expect(screen.getByLabelText(ETIQUETA)).toHaveValue('7'))
    fireEvent.click(screen.getByRole('button', { name: es.modePipeline }))
    expect(screen.getByLabelText(ETIQUETA)).toHaveValue('7')
    fireEvent.click(screen.getByRole('button', { name: es.modeImagen }))
    fireEvent.click(screen.getByRole('button', { name: es.modeChat }))
    expect(useJaxStore.getState().proyectoActivo).toEqual({ id: 7, nombre: 'Siete' })
    await waitFor(() => expect(screen.getByLabelText(ETIQUETA)).toHaveValue('7'))
  })
})
