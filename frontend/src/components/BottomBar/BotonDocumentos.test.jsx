import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'

vi.mock('../../api/proyectos', () => ({
  listarProyectos: vi.fn(), verProyecto: vi.fn(), crearProyecto: vi.fn(),
}))
// El selector real se prueba aparte (SelectorDeDocumentos.test.jsx): aquí solo importa a qué proyecto se abre.
vi.mock('../proyectos/SelectorDeDocumentos', () => ({
  default: ({ proyectoId, nombreProyecto, enVentana, onCerrar }) => (
    <div data-testid="selector-docs" data-proyecto={proyectoId} data-nombre={nombreProyecto} data-ventana={String(!!enVentana)}>
      <button onClick={onCerrar}>cerrar-stub</button>
    </div>
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

  it('mientras el papel está en camino no se ofrece subir', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } })
    let resolver
    verProyecto.mockReturnValue(new Promise((r) => { resolver = r }))
    renderBoton()
    expect(screen.getByRole('button', { name: T.boton })).toBeDisabled()
    resolver(ALFA)
    await waitFor(() => expect(screen.getByRole('button', { name: T.boton })).toBeEnabled())
  })

  it('una consulta fallida deja el botón cerrado, con un texto propio (falla cerrado)', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } })
    verProyecto.mockRejectedValue({ response: { status: 404 } })
    renderBoton()
    const boton = await screen.findByRole('button', { name: T.noSePudoComprobar })
    expect(boton).toBeDisabled()
    expect(boton).toHaveAttribute('title', T.noSePudoComprobar)
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

  // Respuestas controladas a mano: 7 (r1) -> 9 (r2) -> 7 (r3). La r1 llega tarde con OWNER y,
  // como también es del proyecto 7, solo la guarda del pedido vigente impide que habilite el botón.
  it('7→9→7: ni el papel viejo ni una respuesta tardía se aplican; manda la última consulta', async () => {
    const pendientes = []
    verProyecto.mockImplementation(() => new Promise((r) => { pendientes.push(r) }))
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } })
    renderBoton()
    act(() => useJaxStore.setState({ proyectoActivo: { id: 9, nombre: 'Nueve' } }))
    act(() => useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } }))
    expect(pendientes).toHaveLength(3)
    expect(screen.getByRole('button', { name: T.boton })).toBeDisabled()
    await act(async () => { pendientes[0](ALFA) })
    expect(screen.getByRole('button', { name: T.boton })).toBeDisabled()
    await act(async () => { pendientes[1]({ ...ALFA, id: 9 }) })
    expect(screen.getByRole('button', { name: T.boton })).toBeDisabled()
    await act(async () => { pendientes[2]({ ...SOLO_LEE, id: 7 }) })
    expect(screen.getByRole('button', { name: T.sinPermiso })).toBeDisabled()
  })

  it('al volver a un proyecto no se reutiliza su papel anterior mientras llega el nuevo', async () => {
    verProyecto.mockResolvedValueOnce(ALFA)
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } })
    renderBoton()
    await waitFor(() => expect(screen.getByRole('button', { name: T.boton })).toBeEnabled())
    verProyecto.mockReturnValue(new Promise(() => {}))
    act(() => useJaxStore.setState({ proyectoActivo: { id: 9, nombre: 'Nueve' } }))
    act(() => useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } }))
    expect(screen.getByRole('button', { name: T.boton })).toBeDisabled()
  })

  it('proyecto archivado tras una subida: al cerrar el selector el botón queda cerrado y lo dice', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } })
    verProyecto.mockResolvedValueOnce(ALFA)
    renderBoton()
    await waitFor(() => expect(screen.getByRole('button', { name: T.boton })).toBeEnabled())
    verProyecto.mockResolvedValue({ ...ALFA, estado: 'ARCHIVED' })
    fireEvent.click(screen.getByRole('button', { name: T.boton }))
    expect(verProyecto).toHaveBeenCalledTimes(2) // se vuelve a consultar al abrir
    fireEvent.click(screen.getByText('cerrar-stub'))
    const boton = await screen.findByRole('button', { name: T.proyectoArchivado })
    expect(boton).toBeDisabled()
    expect(screen.queryByTestId('selector-docs')).toBeNull()
  })

  it('papel perdido mientras el selector estaba abierto: al cerrarlo el botón dice sinPermiso', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } })
    verProyecto.mockResolvedValueOnce(ALFA)
    renderBoton()
    await waitFor(() => expect(screen.getByRole('button', { name: T.boton })).toBeEnabled())
    verProyecto.mockResolvedValue({ ...ALFA, papel: 'VIEWER' })
    fireEvent.click(screen.getByRole('button', { name: T.boton }))
    fireEvent.click(screen.getByText('cerrar-stub'))
    expect(await screen.findByRole('button', { name: T.sinPermiso })).toBeDisabled()
  })
})

// Ronda final, MAJOR-2: el destino se fijaba al abrir y no se reiniciaba al cambiar de proyecto.
describe('BotonDocumentos -- el destino sigue al proyecto activo', () => {
  it('abre el selector en ventana propia, con el nombre del proyecto de destino', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } })
    verProyecto.mockResolvedValue(ALFA)
    renderBoton()
    await waitFor(() => expect(screen.getByRole('button', { name: T.boton })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: T.boton }))
    const selector = screen.getByTestId('selector-docs')
    expect(selector).toHaveAttribute('data-nombre', 'Alfa')
    expect(selector).toHaveAttribute('data-ventana', 'true')
  })

  it('7→9 con el selector abierto: el flujo se cierra al instante y no se reabre solo para 9', async () => {
    useJaxStore.setState({ proyectoActivo: { id: 7, nombre: 'Alfa' } })
    verProyecto.mockResolvedValue(ALFA)
    renderBoton()
    await waitFor(() => expect(screen.getByRole('button', { name: T.boton })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: T.boton }))
    expect(screen.getByTestId('selector-docs')).toHaveAttribute('data-proyecto', '7')
    verProyecto.mockResolvedValue({ ...ALFA, id: 9 })
    act(() => useJaxStore.setState({ proyectoActivo: { id: 9, nombre: 'Nueve' } }))
    expect(screen.queryByTestId('selector-docs')).toBeNull()
    await waitFor(() => expect(screen.getByRole('button', { name: T.boton })).toBeEnabled())
    expect(screen.queryByTestId('selector-docs')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: T.boton }))
    expect(screen.getByTestId('selector-docs')).toHaveAttribute('data-proyecto', '9')
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

  it('pagina con antesDe: «Cargar más» trae la siguiente página y desaparece al terminar; nunca trunca', async () => {
    const OTRO = { id: 9, nombre: 'Nueve', estado: 'ACTIVE', papel: 'CONTRIBUTOR' }
    listarProyectos.mockResolvedValueOnce({ proyectos: [ALFA], siguiente: 5 })
    listarProyectos.mockResolvedValueOnce({ proyectos: [OTRO], siguiente: null })
    renderBoton()
    fireEvent.click(screen.getByRole('button', { name: T.boton }))
    const dialogo = await screen.findByRole('dialog')
    await within(dialogo).findByRole('button', { name: 'Alfa' })
    fireEvent.click(within(dialogo).getByRole('button', { name: es.proyectos.cargarMas }))
    await within(dialogo).findByRole('button', { name: 'Nueve' })
    expect(listarProyectos).toHaveBeenLastCalledWith({ vista: 'activos', limite: 100, antesDe: 5 })
    expect(within(dialogo).getByRole('button', { name: 'Alfa' })).toBeInTheDocument()
    expect(within(dialogo).queryByRole('button', { name: es.proyectos.cargarMas })).toBeNull()
  })

  it('una página sin proyectos editables no dice «ninguno» si hay más páginas', async () => {
    listarProyectos.mockResolvedValue({ proyectos: [SOLO_LEE], siguiente: 3 })
    renderBoton()
    fireEvent.click(screen.getByRole('button', { name: T.boton }))
    const dialogo = await screen.findByRole('dialog')
    await within(dialogo).findByRole('button', { name: es.proyectos.cargarMas })
    expect(within(dialogo).queryByText(T.elegirProyecto.ninguno)).toBeNull()
  })

  it('si falla «Cargar más» conserva lo ya listado y avisa', async () => {
    listarProyectos.mockResolvedValueOnce({ proyectos: [ALFA], siguiente: 5 })
    listarProyectos.mockRejectedValueOnce(new Error('x'))
    renderBoton()
    fireEvent.click(screen.getByRole('button', { name: T.boton }))
    const dialogo = await screen.findByRole('dialog')
    await within(dialogo).findByRole('button', { name: 'Alfa' })
    fireEvent.click(within(dialogo).getByRole('button', { name: es.proyectos.cargarMas }))
    expect(await within(dialogo).findByRole('alert')).toBeInTheDocument()
    expect(within(dialogo).getByRole('button', { name: 'Alfa' })).toBeInTheDocument()
    expect(within(dialogo).getByRole('button', { name: es.proyectos.cargarMas })).toBeEnabled()
  })
})
