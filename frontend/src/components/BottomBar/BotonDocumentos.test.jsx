import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'

vi.mock('../../api/proyectos', () => ({
  listarProyectos: vi.fn(), verProyecto: vi.fn(), crearProyecto: vi.fn(),
}))
// El selector real se prueba aparte (SelectorDeDocumentos.test.jsx): aquí solo importa a qué proyecto se abre.
vi.mock('../proyectos/SelectorDeDocumentos', () => ({
  default: ({ proyectoId, onCerrar }) => (
    <div data-testid="selector-docs" data-proyecto={proyectoId}><button onClick={onCerrar}>cerrar-stub</button></div>
  ),
}))

import { listarProyectos, verProyecto, crearProyecto } from '../../api/proyectos'
import BotonDocumentos from './BotonDocumentos'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import es from '../../i18n/es.js'

const T = es.proyectos.documentos
const INICIAL = useJaxStore.getState()
const ALFA = { id: 7, nombre: 'Alfa', estado: 'ACTIVE', papel: 'OWNER' }
const SOLO_LEE = { id: 8, nombre: 'Beta', estado: 'ACTIVE', papel: 'VIEWER' }

function renderBoton() {
  return render(<I18nProvider><MemoryRouter><BotonDocumentos /></MemoryRouter></I18nProvider>)
}

beforeEach(() => {
  localStorage.clear()
  useJaxStore.setState({ ...INICIAL, proyectoActivo: null, toasts: [] }, true)
  listarProyectos.mockReset()
  verProyecto.mockReset()
  crearProyecto.mockReset()
})

describe('BotonDocumentos -- con proyecto elegido', () => {
  it('papel con permiso y proyecto activo: abre el selector de ESE proyecto', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } })
    verProyecto.mockResolvedValue(ALFA)
    renderBoton()
    const boton = screen.getByRole('button', { name: T.boton })
    await waitFor(() => expect(boton).toBeEnabled())
    fireEvent.click(boton)
    expect(screen.getByTestId('selector-docs')).toHaveAttribute('data-proyecto', '7')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('VIEWER: deshabilitado, con sinPermiso en title y aria-label', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 8, nombre: 'Beta' } })
    verProyecto.mockResolvedValue(SOLO_LEE)
    renderBoton()
    const boton = await screen.findByRole('button', { name: T.sinPermiso })
    expect(boton).toBeDisabled()
    expect(boton).toHaveAttribute('title', T.sinPermiso)
    fireEvent.click(boton)
    expect(screen.queryByTestId('selector-docs')).toBeNull()
  })

  it('proyecto archivado: deshabilitado con su propio texto', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } })
    verProyecto.mockResolvedValue({ ...ALFA, estado: 'ARCHIVED' })
    renderBoton()
    const boton = await screen.findByRole('button', { name: T.proyectoArchivado })
    expect(boton).toBeDisabled()
    expect(boton).toHaveAttribute('title', T.proyectoArchivado)
  })

  it('mientras no se sabe el papel (o si la consulta falla) no se ofrece subir', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } })
    let resolver
    verProyecto.mockReturnValue(new Promise((r) => { resolver = r }))
    renderBoton()
    expect(screen.getByRole('button', { name: T.boton })).toBeDisabled()
    resolver(ALFA)
    await waitFor(() => expect(screen.getByRole('button', { name: T.boton })).toBeEnabled())
  })

  it('una consulta fallida deja el botón cerrado (falla cerrado)', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } })
    verProyecto.mockRejectedValue({ response: { status: 404 } })
    renderBoton()
    await waitFor(() => expect(verProyecto).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: T.boton })).toBeDisabled()
  })

  it('pide el proyecto una vez por cambio de proyecto, no por render', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } })
    verProyecto.mockResolvedValue(ALFA)
    const { rerender } = renderBoton()
    await waitFor(() => expect(screen.getByRole('button', { name: T.boton })).toBeEnabled())
    rerender(<I18nProvider><MemoryRouter><BotonDocumentos /></MemoryRouter></I18nProvider>)
    expect(verProyecto).toHaveBeenCalledTimes(1)
    verProyecto.mockResolvedValue({ ...SOLO_LEE, id: 9 })
    useJaxStore.setState({ proyectoActivo: { id: 9, nombre: 'Nueve' } })
    await screen.findByRole('button', { name: T.sinPermiso })
    expect(verProyecto).toHaveBeenCalledTimes(2)
    expect(verProyecto).toHaveBeenLastCalledWith(9)
  })

  it('un papel que llega tarde de OTRO proyecto no se aplica al actual', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } })
    let resolverViejo
    verProyecto.mockReturnValueOnce(new Promise((r) => { resolverViejo = r }))
    renderBoton()
    verProyecto.mockResolvedValueOnce({ ...SOLO_LEE, id: 9 })
    useJaxStore.setState({ proyectoActivo: { id: 9, nombre: 'Nueve' } })
    await screen.findByRole('button', { name: T.sinPermiso })
    resolverViejo(ALFA)
    await Promise.resolve()
    expect(screen.getByRole('button', { name: T.sinPermiso })).toBeDisabled()
  })
})

describe('BotonDocumentos -- en «Personal»', () => {
  it('abre elegirProyecto con los proyectos activos donde se puede agregar; al elegir, sigue al selector', async () => {
    listarProyectos.mockResolvedValue({ proyectos: [ALFA, SOLO_LEE] })
    verProyecto.mockResolvedValue(ALFA)
    renderBoton()
    fireEvent.click(screen.getByRole('button', { name: T.boton }))
    const dialogo = await screen.findByRole('dialog', { name: T.elegirProyecto.titulo })
    expect(within(dialogo).getByText(T.elegirProyecto.texto)).toBeInTheDocument()
    await within(dialogo).findByRole('button', { name: 'Alfa' })
    expect(within(dialogo).queryByRole('button', { name: 'Beta' })).toBeNull()
    expect(listarProyectos).toHaveBeenCalledWith({ vista: 'activos', limite: 100 })
    fireEvent.click(within(dialogo).getByRole('button', { name: 'Alfa' }))
    expect(useJaxStore.getState().proyectoActivo).toEqual({ id: 7, nombre: 'Alfa' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByTestId('selector-docs')).toHaveAttribute('data-proyecto', '7')
  })

  it('sin proyectos donde agregar lo dice, y «Crear un proyecto» sigue disponible', async () => {
    listarProyectos.mockResolvedValue({ proyectos: [SOLO_LEE] })
    renderBoton()
    fireEvent.click(screen.getByRole('button', { name: T.boton }))
    const dialogo = await screen.findByRole('dialog')
    expect(await within(dialogo).findByText(T.elegirProyecto.ninguno)).toBeInTheDocument()
    expect(within(dialogo).getByRole('button', { name: T.elegirProyecto.crear })).toBeEnabled()
  })

  it('si la lista falla muestra el error y deja crear', async () => {
    listarProyectos.mockRejectedValue(new Error('x'))
    renderBoton()
    fireEvent.click(screen.getByRole('button', { name: T.boton }))
    const dialogo = await screen.findByRole('dialog')
    expect(await within(dialogo).findByRole('alert')).toHaveTextContent(es.proyectos.errores.generico)
    expect(within(dialogo).getByRole('button', { name: T.elegirProyecto.crear })).toBeEnabled()
  })

  it('«Crear un proyecto» abre el alta; el proyecto nuevo queda activo y sigue al selector', async () => {
    listarProyectos.mockResolvedValue({ proyectos: [] })
    crearProyecto.mockResolvedValue({ id: 21, nombre: 'Nuevo', estado: 'ACTIVE', papel: 'OWNER' })
    verProyecto.mockResolvedValue({ id: 21, nombre: 'Nuevo', estado: 'ACTIVE', papel: 'OWNER' })
    renderBoton()
    fireEvent.click(screen.getByRole('button', { name: T.boton }))
    const elegir = await screen.findByRole('dialog', { name: T.elegirProyecto.titulo })
    fireEvent.click(within(elegir).getByRole('button', { name: T.elegirProyecto.crear }))
    const alta = await screen.findByRole('dialog', { name: es.proyectos.nuevo })
    fireEvent.change(within(alta).getByLabelText(es.proyectos.nombre), { target: { value: 'Nuevo' } })
    fireEvent.click(within(alta).getByRole('button', { name: es.proyectos.crear }))
    await waitFor(() => expect(screen.getByTestId('selector-docs')).toHaveAttribute('data-proyecto', '21'))
    expect(useJaxStore.getState().proyectoActivo).toEqual({ id: 21, nombre: 'Nuevo' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
