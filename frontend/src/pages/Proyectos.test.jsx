import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'
import '@testing-library/jest-dom'

// Pantalla /proyectos (E1, T7): lista con pestañas, «Cargar más» y crear.
vi.mock('../api/proyectos', () => ({
  listarProyectos: vi.fn(),
  crearProyecto: vi.fn(),
}))

import { listarProyectos, crearProyecto } from '../api/proyectos'
import Proyectos from './Proyectos'
import { I18nProvider } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
import es from '../i18n/es.js'

const T = es.proyectos
const INICIAL = useJaxStore.getState()

const P1 = { id: 1, uuid: 'u1', nombre: 'Alfa', descripcion: null, estado: 'ACTIVE', papel: 'OWNER' }
const P2 = { id: 2, uuid: 'u2', nombre: 'Beta', descripcion: 'segundo', estado: 'ACTIVE', papel: 'VIEWER' }
const P3 = { id: 3, uuid: 'u3', nombre: 'Gamma', descripcion: null, estado: 'ACTIVE', papel: 'CONTRIBUTOR' }

function Ruta() {
  return <div data-testid="ruta">{useLocation().pathname}</div>
}

function renderProyectos() {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={['/proyectos']}>
        <Ruta />
        <Routes>
          <Route path="/proyectos" element={<Proyectos />} />
          <Route path="/proyectos/:id" element={<div>detalle</div>} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  )
}

function abrirModal() {
  fireEvent.click(screen.getByRole('button', { name: T.nuevo }))
  return screen.getByRole('dialog')
}

let espias
beforeEach(() => {
  useJaxStore.setState({ ...INICIAL, token: 't', user: { user_id: 1, email: 'a@b.c', role: 'operator' } }, true)
  listarProyectos.mockReset()
  crearProyecto.mockReset()
  listarProyectos.mockResolvedValue({ proyectos: [P1, P2], siguiente: null })
  espias = ['confirm', 'alert', 'prompt'].map((m) => vi.spyOn(window, m).mockImplementation(() => true))
})
afterEach(() => {
  espias.forEach((e) => e.mockRestore())
  vi.restoreAllMocks()
})

describe('Proyectos', () => {
  it('pide la vista activos y muestra la lista', async () => {
    renderProyectos()
    expect(await screen.findByText('Alfa')).toBeInTheDocument()
    expect(screen.getByText('Beta')).toBeInTheDocument()
    expect(listarProyectos).toHaveBeenCalledWith({ vista: 'activos', antesDe: null })
  })

  it('lista vacía muestra el texto de vacío', async () => {
    listarProyectos.mockResolvedValue({ proyectos: [], siguiente: null })
    renderProyectos()
    expect(await screen.findByText(T.vacio)).toBeInTheDocument()
  })

  it('cambiar de pestaña pide vista=archivados', async () => {
    renderProyectos()
    await screen.findByText('Alfa')
    fireEvent.click(screen.getByRole('tab', { name: T.vistas.archivados }))
    await waitFor(() => expect(listarProyectos).toHaveBeenLastCalledWith({ vista: 'archivados', antesDe: null }))
  })

  it('un no-admin no ve la pestaña Ocultos', async () => {
    renderProyectos()
    await screen.findByText('Alfa')
    expect(screen.queryByRole('tab', { name: T.vistas.ocultos })).not.toBeInTheDocument()
  })

  it('un admin ve Ocultos y pide vista=ocultos', async () => {
    useJaxStore.setState({ user: { user_id: 1, email: 'a@b.c', role: 'superadmin' } })
    renderProyectos()
    await screen.findByText('Alfa')
    fireEvent.click(screen.getByRole('tab', { name: T.vistas.ocultos }))
    await waitFor(() => expect(listarProyectos).toHaveBeenLastCalledWith({ vista: 'ocultos', antesDe: null }))
  })

  it('«Cargar más» pide antesDe=siguiente y agrega al final; desaparece sin siguiente', async () => {
    listarProyectos
      .mockResolvedValueOnce({ proyectos: [P1, P2], siguiente: 2 })
      .mockResolvedValueOnce({ proyectos: [P3], siguiente: null })
    renderProyectos()
    await screen.findByText('Alfa')
    fireEvent.click(screen.getByRole('button', { name: T.cargarMas }))
    expect(await screen.findByText('Gamma')).toBeInTheDocument()
    expect(listarProyectos).toHaveBeenLastCalledWith({ vista: 'activos', antesDe: 2 })
    expect(screen.getByText('Alfa')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: T.cargarMas })).not.toBeInTheDocument()
  })

  it('si la carga falla muestra el error traducido y reintenta', async () => {
    listarProyectos.mockRejectedValueOnce({ response: { data: { detail: { code: 'reintentar' } } } })
    renderProyectos()
    expect(await screen.findByText(T.errores.reintentar)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: T.reintentar }))
    expect(await screen.findByText('Alfa')).toBeInTheDocument()
  })

  it('una respuesta tardía de otra pestaña no pisa la actual', async () => {
    let resolverActivos
    listarProyectos.mockImplementationOnce(() => new Promise((r) => { resolverActivos = r }))
    listarProyectos.mockResolvedValueOnce({ proyectos: [P3], siguiente: null })
    renderProyectos()
    fireEvent.click(screen.getByRole('tab', { name: T.vistas.archivados }))
    expect(await screen.findByText('Gamma')).toBeInTheDocument()
    resolverActivos({ proyectos: [P1], siguiente: null })
    await Promise.resolve()
    await Promise.resolve()
    expect(screen.queryByText('Alfa')).not.toBeInTheDocument()
  })

  it('crear: doble clic manda una sola petición, con una llave; navega al detalle', async () => {
    let resolver
    crearProyecto.mockImplementation(() => new Promise((r) => { resolver = r }))
    renderProyectos()
    await screen.findByText('Alfa')
    const dialogo = abrirModal()
    fireEvent.change(within(dialogo).getByLabelText(T.nombre), { target: { value: 'Nuevo' } })
    const crear = within(dialogo).getByRole('button', { name: T.crear })
    fireEvent.click(crear)
    fireEvent.click(crear)
    expect(crearProyecto).toHaveBeenCalledTimes(1)
    const [datos, llave] = crearProyecto.mock.calls[0]
    expect(datos).toEqual({ nombre: 'Nuevo', descripcion: null })
    expect(llave).toMatch(/^[0-9a-f-]{36}$/)
    expect(crear).toBeDisabled()
    resolver({ id: 9, uuid: 'u9', nombre: 'Nuevo', descripcion: null, estado: 'ACTIVE', papel: 'OWNER' })
    await waitFor(() => expect(screen.getByTestId('ruta')).toHaveTextContent('/proyectos/9'))
  })

  it('un 409 muestra el texto traducido y el reintento reusa la misma llave', async () => {
    crearProyecto.mockRejectedValueOnce({ response: { status: 409, data: { detail: { code: 'estado_no_permite' } } } })
    crearProyecto.mockResolvedValueOnce({ id: 9, nombre: 'Nuevo' })
    renderProyectos()
    await screen.findByText('Alfa')
    const dialogo = abrirModal()
    fireEvent.change(within(dialogo).getByLabelText(T.nombre), { target: { value: 'Nuevo' } })
    fireEvent.click(within(dialogo).getByRole('button', { name: T.crear }))
    expect(await within(dialogo).findByText(T.errores.estado_no_permite)).toBeInTheDocument()
    await waitFor(() => expect(within(dialogo).getByRole('button', { name: T.crear })).not.toBeDisabled())
    fireEvent.click(within(dialogo).getByRole('button', { name: T.crear }))
    await waitFor(() => expect(crearProyecto).toHaveBeenCalledTimes(2))
    expect(crearProyecto.mock.calls[1][1]).toBe(crearProyecto.mock.calls[0][1])
  })

  it('error, editar el nombre y reintentar usa una llave distinta', async () => {
    crearProyecto.mockRejectedValueOnce({ response: { status: 409, data: { detail: { code: 'estado_no_permite' } } } })
    crearProyecto.mockResolvedValueOnce({ id: 9, nombre: 'Corregido' })
    renderProyectos()
    await screen.findByText('Alfa')
    const dialogo = abrirModal()
    fireEvent.change(within(dialogo).getByLabelText(T.nombre), { target: { value: 'Nuevo' } })
    fireEvent.click(within(dialogo).getByRole('button', { name: T.crear }))
    await within(dialogo).findByText(T.errores.estado_no_permite)
    await waitFor(() => expect(within(dialogo).getByRole('button', { name: T.crear })).not.toBeDisabled())
    fireEvent.change(within(dialogo).getByLabelText(T.nombre), { target: { value: 'Corregido' } })
    fireEvent.click(within(dialogo).getByRole('button', { name: T.crear }))
    await waitFor(() => expect(crearProyecto).toHaveBeenCalledTimes(2))
    expect(crearProyecto.mock.calls[1][1]).not.toBe(crearProyecto.mock.calls[0][1])
  })

  it('un código desconocido cae en el texto genérico', async () => {
    crearProyecto.mockRejectedValueOnce({ response: { data: { detail: { code: 'inventado' } } } })
    renderProyectos()
    await screen.findByText('Alfa')
    const dialogo = abrirModal()
    fireEvent.change(within(dialogo).getByLabelText(T.nombre), { target: { value: 'X' } })
    fireEvent.click(within(dialogo).getByRole('button', { name: T.crear }))
    expect(await within(dialogo).findByText(T.errores.generico)).toBeInTheDocument()
  })

  it('cada apertura del modal genera una llave nueva', async () => {
    crearProyecto.mockRejectedValue({ response: { data: { detail: { code: 'estado_no_permite' } } } })
    renderProyectos()
    await screen.findByText('Alfa')
    for (let i = 0; i < 2; i += 1) {
      const dialogo = abrirModal()
      fireEvent.change(within(dialogo).getByLabelText(T.nombre), { target: { value: 'X' } })
      fireEvent.click(within(dialogo).getByRole('button', { name: T.crear }))
      await within(dialogo).findByText(T.errores.estado_no_permite)
      fireEvent.click(within(dialogo).getByRole('button', { name: T.cancelar }))
    }
    expect(crearProyecto.mock.calls[1][1]).not.toBe(crearProyecto.mock.calls[0][1])
  })

  it('Escape cierra el modal y el foco vuelve al botón Nuevo proyecto', async () => {
    renderProyectos()
    await screen.findByText('Alfa')
    const boton = screen.getByRole('button', { name: T.nuevo })
    boton.focus()
    abrirModal()
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(boton).toHaveFocus()
  })

  it('no usa confirm, alert ni prompt del navegador', async () => {
    crearProyecto.mockRejectedValueOnce({ response: { data: { detail: { code: 'estado_no_permite' } } } })
    renderProyectos()
    await screen.findByText('Alfa')
    const dialogo = abrirModal()
    fireEvent.change(within(dialogo).getByLabelText(T.nombre), { target: { value: 'X' } })
    fireEvent.click(within(dialogo).getByRole('button', { name: T.crear }))
    await within(dialogo).findByText(T.errores.estado_no_permite)
    fireEvent.click(within(dialogo).getByRole('button', { name: T.cancelar }))
    espias.forEach((e) => expect(e).not.toHaveBeenCalled())
  })
})
