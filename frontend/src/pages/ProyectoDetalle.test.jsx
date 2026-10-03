import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import '@testing-library/jest-dom'

// Detalle de proyecto (E1, T8): Miembros y Ajustes.
vi.mock('../api/proyectos', () => ({
  verProyecto: vi.fn(),
  renombrarProyecto: vi.fn(),
  cambiarEstado: vi.fn(),
  listarMiembros: vi.fn(),
  invitarMiembro: vi.fn(),
  cambiarPapel: vi.fn(),
  quitarMiembro: vi.fn(),
  buscarCandidatos: vi.fn(),
}))

import * as api from '../api/proyectos'
import ProyectoDetalle from './ProyectoDetalle'
import { I18nProvider } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
import es from '../i18n/es.js'
import { TAMANO_BOTON_44 } from '../tema/botones'

const T = es.proyectos
const INICIAL = useJaxStore.getState()

const PROY = { id: 7, uuid: 'u7', nombre: 'Alfa', descripcion: 'desc', estado: 'ACTIVE', papel: 'OWNER' }
const M_YO = { user_id: 1, email: 'yo@x.com', papel: 'OWNER', origen: 'DIRECT' }
const M_ADMIN = { user_id: 2, email: 'admin@x.com', papel: 'OWNER', origen: 'TENANT_ADMIN' }
const M_LECTOR = { user_id: 3, email: 'lec@x.com', papel: 'VIEWER', origen: 'DIRECT' }

function error(status, code) {
  return Object.assign(new Error('x'), { response: { status, data: { detail: { code } } } })
}

function renderDetalle(id = '7') {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={[`/proyectos/${id}`]}>
        <Routes>
          <Route path="/proyectos/:id" element={<ProyectoDetalle />} />
          <Route path="/proyectos" element={<div>lista</div>} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  )
}

function configurar({ proyecto = PROY, miembros = [M_YO, M_ADMIN, M_LECTOR] } = {}) {
  api.verProyecto.mockResolvedValue(proyecto)
  api.listarMiembros.mockResolvedValue({ miembros })
  api.buscarCandidatos.mockResolvedValue({ candidatos: [] })
}

let espias
beforeEach(() => {
  useJaxStore.setState({ ...INICIAL, token: 't', user: { user_id: 1, email: 'yo@x.com', role: 'operator' } }, true)
  Object.values(api).forEach((f) => f.mockReset())
  configurar()
  espias = ['confirm', 'alert', 'prompt'].map((m) => vi.spyOn(window, m).mockImplementation(() => true))
})
afterEach(() => {
  espias.forEach((e) => e.mockRestore())
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('ProyectoDetalle: carga y 404', () => {
  it('muestra nombre, estado y papel, y llama con el id numérico', async () => {
    renderDetalle()
    expect(await screen.findByRole('heading', { name: 'Alfa' })).toBeInTheDocument()
    expect(screen.getByText(new RegExp(`${T.estados.ACTIVE}.*${T.tuPapel}: ${T.papeles.OWNER}`))).toBeInTheDocument()
    expect(api.verProyecto).toHaveBeenCalledWith(7)
    expect(api.listarMiembros).toHaveBeenCalledWith(7)
  })

  // Fernando (2026-10-02): «← Volver a proyectos» sigue el patrón de las demás
  // pantallas (Historial, Proyectos): arriba, en la fila del título, a la
  // derecha, con el estilo del enlace «Volver» de Proyectos.
  it('«Volver a proyectos» va en la fila del título, a la derecha, como en Historial y Proyectos', async () => {
    renderDetalle()
    const titulo = await screen.findByRole('heading', { name: 'Alfa' })
    const volver = screen.getByRole('link', { name: new RegExp(T.volverAProyectos) })
    const fila = titulo.parentElement
    expect(fila.className).toMatch(/\bflex\b/)
    expect(fila.className).toMatch(/justify-between/)
    expect(fila.className).toMatch(/\bmb-6\b/)
    expect(fila.firstElementChild).toBe(titulo)
    expect(fila.lastElementChild).toBe(volver)
    expect(volver.className).toMatch(/text-texto-tenue/)
    expect(volver.className).toMatch(/hover:text-texto\b/)
    expect(volver.className).toMatch(/gap-1\.5/)
    expect(volver.className).not.toMatch(/-ml-/)
    expect(volver.className).not.toMatch(/\bmb-4\b/)
    expect(volver).toHaveAttribute('href', '/proyectos')
  })

  it('el enlace «Volver» está desde la carga, antes de que llegue el proyecto', () => {
    api.verProyecto.mockReturnValue(new Promise(() => {}))
    renderDetalle()
    expect(screen.getByRole('link', { name: new RegExp(T.volverAProyectos) })).toBeInTheDocument()
  })

  it('un 404 muestra solo «no encontrado» y el enlace a /proyectos', async () => {
    api.verProyecto.mockRejectedValue(error(404, 'proyecto_no_encontrado'))
    renderDetalle()
    expect(await screen.findByText(T.noEncontrado)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: T.volverAProyectos })).toHaveAttribute('href', '/proyectos')
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
  })

  it.each(['abc', '0', '-3', '1.5', '7x', '1e2', '0x10'])('un id inválido (%s) es «no encontrado» sin llamar a la API', async (id) => {
    renderDetalle(id)
    expect(await screen.findByText(T.noEncontrado)).toBeInTheDocument()
    expect(api.verProyecto).not.toHaveBeenCalled()
    expect(api.listarMiembros).not.toHaveBeenCalled()
  })

  it('un error que no es 404 ofrece reintentar y no dice «no encontrado»', async () => {
    api.verProyecto.mockRejectedValueOnce(error(500, 'proyectos_error'))
    renderDetalle()
    expect(await screen.findByText(T.errores.proyectos_error)).toBeInTheDocument()
    expect(screen.queryByText(T.noEncontrado)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: T.reintentar }))
    expect(await screen.findByRole('heading', { name: 'Alfa' })).toBeInTheDocument()
  })
})

describe('ProyectoDetalle: pestañas', () => {
  it('roles, aria-controls, aria-selected y flechas', async () => {
    renderDetalle()
    await screen.findByRole('heading', { name: 'Alfa' })
    const lista = screen.getByRole('tablist')
    const tabs = within(lista).getAllByRole('tab')
    expect(tabs.map((x) => x.textContent)).toEqual([T.miembros, T.ajustes])
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    expect(tabs[0]).toHaveAttribute('aria-controls', 'panel-miembros')
    expect(screen.getByRole('tabpanel')).toHaveAttribute('id', 'panel-miembros')
    expect(tabs[1]).toHaveAttribute('tabindex', '-1')

    tabs[0].focus()
    fireEvent.keyDown(tabs[0], { key: 'ArrowRight' })
    expect(tabs[1]).toHaveAttribute('aria-selected', 'true')
    expect(tabs[1]).toHaveFocus()
    expect(screen.getByRole('tabpanel')).toHaveAttribute('id', 'panel-ajustes')
    fireEvent.keyDown(tabs[1], { key: 'ArrowRight' })
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(tabs[0], { key: 'End' })
    expect(tabs[1]).toHaveFocus()
    fireEvent.keyDown(tabs[1], { key: 'Home' })
    expect(tabs[0]).toHaveFocus()
  })

  it('los botones usan el tamaño de 44px sin la clase min-h-6 que lo anula', () => {
    expect(TAMANO_BOTON_44).toMatch(/\bmin-h-11\b/)
    expect(TAMANO_BOTON_44).not.toMatch(/\bmin-h-6\b/)
  })
})

describe('Miembros', () => {
  it('un VIEWER no ve controles ni pestaña de ajustes', async () => {
    configurar({ proyecto: { ...PROY, papel: 'VIEWER' } })
    renderDetalle()
    await screen.findByText('lec@x.com')
    expect(screen.queryByRole('button', { name: T.quitar })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(T.buscarPorEmail)).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: T.ajustes })).not.toBeInTheDocument()
  })

  it('un OWNER con el proyecto archivado no ve controles de miembros', async () => {
    configurar({ proyecto: { ...PROY, estado: 'ARCHIVED' } })
    renderDetalle()
    await screen.findByText('lec@x.com')
    expect(screen.queryByRole('button', { name: T.quitar })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(T.buscarPorEmail)).not.toBeInTheDocument()
  })

  it('las filas TENANT_ADMIN no llevan controles; las demás sí', async () => {
    renderDetalle()
    const fila = (await screen.findByText('admin@x.com')).closest('li')
    expect(within(fila).queryByRole('button')).not.toBeInTheDocument()
    expect(within(fila).queryByRole('combobox')).not.toBeInTheDocument()
    expect(within(fila).getByText(T.origenProtegido, { exact: false })).toBeInTheDocument()
    const otra = screen.getByText('lec@x.com').closest('li')
    expect(within(otra).getByRole('button', { name: T.quitar })).toBeInTheDocument()
    expect(within(otra).getByRole('combobox')).toBeInTheDocument()
  })

  it('cambiar el papel del último dueño muestra el texto de ultimo_dueno y recarga', async () => {
    api.cambiarPapel.mockRejectedValue(error(409, 'ultimo_dueno'))
    renderDetalle()
    const fila = (await screen.findByText('yo@x.com')).closest('li')
    const cargas = api.verProyecto.mock.calls.length
    fireEvent.change(within(fila).getByRole('combobox'), { target: { value: 'VIEWER' } })
    expect(await screen.findByText(T.errores.ultimo_dueno)).toBeInTheDocument()
    expect(api.cambiarPapel).toHaveBeenCalledWith(7, 1, 'VIEWER')
    await waitFor(() => expect(api.verProyecto.mock.calls.length).toBe(cargas + 1))
    expect(within(fila).getByRole('combobox')).toHaveValue('OWNER')
  })

  it('quitar pide confirmación en Dialogo y no llama a la API si se cancela', async () => {
    renderDetalle()
    const fila = (await screen.findByText('lec@x.com')).closest('li')
    fireEvent.click(within(fila).getByRole('button', { name: T.quitar }))
    const dialogo = screen.getByRole('dialog')
    expect(within(dialogo).getByText(T.confirmaQuitar('lec@x.com'))).toBeInTheDocument()
    fireEvent.click(within(dialogo).getByRole('button', { name: T.cancelar }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.quitarMiembro).not.toHaveBeenCalled()
  })

  it('quitar confirmado llama a quitarMiembro y recarga', async () => {
    api.quitarMiembro.mockResolvedValue({})
    renderDetalle()
    const fila = (await screen.findByText('lec@x.com')).closest('li')
    const cargas = api.listarMiembros.mock.calls.length
    fireEvent.click(within(fila).getByRole('button', { name: T.quitar }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: T.quitar }))
    await waitFor(() => expect(api.quitarMiembro).toHaveBeenCalledWith(7, 3))
    await waitFor(() => expect(api.listarMiembros.mock.calls.length).toBe(cargas + 1))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

describe('Ajustes', () => {
  async function irAAjustes() {
    await screen.findByRole('heading', { name: 'Alfa' })
    fireEvent.click(screen.getByRole('tab', { name: T.ajustes }))
  }

  it('renombrar envía siempre nombre y descripción, y recarga', async () => {
    api.renombrarProyecto.mockResolvedValue({})
    renderDetalle()
    await irAAjustes()
    fireEvent.change(screen.getByLabelText(T.nombre), { target: { value: '  Nuevo  ' } })
    fireEvent.change(screen.getByLabelText(T.descripcion), { target: { value: '' } })
    const cargas = api.verProyecto.mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: T.guardar }))
    await waitFor(() => expect(api.renombrarProyecto).toHaveBeenCalledWith(7, { nombre: 'Nuevo', descripcion: null }))
    await waitFor(() => expect(api.verProyecto.mock.calls.length).toBe(cargas + 1))
  })

  it('un nombre de solo espacios deshabilita Guardar', async () => {
    renderDetalle()
    await irAAjustes()
    const guardar = screen.getByRole('button', { name: T.guardar })
    expect(guardar).toBeEnabled()
    fireEvent.change(screen.getByLabelText(T.nombre), { target: { value: '   ' } })
    expect(guardar).toBeDisabled()
    fireEvent.click(guardar)
    expect(api.renombrarProyecto).not.toHaveBeenCalled()
  })

  it('un OWNER con el proyecto ARCHIVED no ve el formulario de renombrar ni Guardar', async () => {
    configurar({ proyecto: { ...PROY, estado: 'ARCHIVED' } })
    renderDetalle()
    await irAAjustes()
    expect(screen.queryByLabelText(T.nombre)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: T.guardar })).not.toBeInTheDocument()
  })

  it('un admin con el proyecto HIDDEN no ve el formulario de renombrar ni Guardar', async () => {
    useJaxStore.setState({ ...useJaxStore.getState(), user: { user_id: 1, email: 'a@b.c', role: 'superadmin' } })
    configurar({ proyecto: { ...PROY, estado: 'HIDDEN' } })
    renderDetalle()
    await irAAjustes()
    expect(screen.queryByLabelText(T.nombre)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: T.guardar })).not.toBeInTheDocument()
  })

  it('archivar pide confirmación y no llama a la API si se cancela', async () => {
    renderDetalle()
    await irAAjustes()
    fireEvent.click(screen.getByRole('button', { name: T.archivar }))
    const dialogo = screen.getByRole('dialog')
    expect(within(dialogo).getByText(T.confirmaArchivar('Alfa'))).toBeInTheDocument()
    fireEvent.click(within(dialogo).getByRole('button', { name: T.cancelar }))
    expect(api.cambiarEstado).not.toHaveBeenCalled()
  })

  it('archivar confirmado pone ARCHIVED y recarga', async () => {
    api.cambiarEstado.mockResolvedValue({})
    renderDetalle()
    await irAAjustes()
    const cargas = api.verProyecto.mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: T.archivar }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: T.archivar }))
    await waitFor(() => expect(api.cambiarEstado).toHaveBeenCalledWith(7, 'ARCHIVED'))
    await waitFor(() => expect(api.verProyecto.mock.calls.length).toBe(cargas + 1))
  })

  it('un error de estado_no_permite se muestra traducido', async () => {
    api.cambiarEstado.mockRejectedValue(error(409, 'estado_no_permite'))
    renderDetalle()
    await irAAjustes()
    fireEvent.click(screen.getByRole('button', { name: T.archivar }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: T.archivar }))
    expect(await screen.findByText(T.errores.estado_no_permite)).toBeInTheDocument()
  })

  it('archivado: Restaurar vuelve a ACTIVE; Ocultar no aparece sin ser admin', async () => {
    configurar({ proyecto: { ...PROY, estado: 'ARCHIVED' } })
    api.cambiarEstado.mockResolvedValue({})
    renderDetalle()
    await irAAjustes()
    expect(screen.queryByRole('button', { name: T.ocultar })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: T.restaurar }))
    await waitFor(() => expect(api.cambiarEstado).toHaveBeenCalledWith(7, 'ACTIVE'))
  })

  it('un admin oculta con ConfirmacionSuma: sin la suma correcta no llama a la API', async () => {
    useJaxStore.setState({ ...useJaxStore.getState(), user: { user_id: 1, email: 'a@b.c', role: 'superadmin' } })
    configurar({ proyecto: { ...PROY, estado: 'ARCHIVED' } })
    api.cambiarEstado.mockResolvedValue({})
    renderDetalle()
    await irAAjustes()
    fireEvent.click(screen.getByRole('button', { name: T.ocultar }))
    const dialogo = screen.getByRole('dialog')
    expect(within(dialogo).getByText(T.confirmaOcultar('Alfa'))).toBeInTheDocument()
    const confirmar = within(dialogo).getByRole('button', { name: T.ocultar })
    expect(confirmar).toBeDisabled()
    fireEvent.change(within(dialogo).getByRole('spinbutton'), { target: { value: '0' } })
    expect(confirmar).toBeDisabled()
    expect(api.cambiarEstado).not.toHaveBeenCalled()
    const [a, b] = within(dialogo).getByText(/\d+\s*\+\s*\d+/).textContent.match(/\d+/g).map(Number)
    fireEvent.change(within(dialogo).getByRole('spinbutton'), { target: { value: String(a + b) } })
    fireEvent.click(confirmar)
    await waitFor(() => expect(api.cambiarEstado).toHaveBeenCalledWith(7, 'HIDDEN'))
  })

  it('un admin ve Mostrar en un proyecto oculto y lo pasa a ARCHIVED', async () => {
    useJaxStore.setState({ ...useJaxStore.getState(), user: { user_id: 1, email: 'a@b.c', role: 'superadmin' } })
    configurar({ proyecto: { ...PROY, estado: 'HIDDEN' } })
    api.cambiarEstado.mockResolvedValue({})
    renderDetalle()
    await irAAjustes()
    expect(screen.queryByRole('button', { name: T.ocultar })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: T.mostrar }))
    await waitFor(() => expect(api.cambiarEstado).toHaveBeenCalledWith(7, 'ARCHIVED'))
  })

  it('un no-admin no ve Ocultar ni Mostrar', async () => {
    configurar({ proyecto: { ...PROY, estado: 'HIDDEN' } })
    renderDetalle()
    await irAAjustes()
    expect(screen.queryByRole('button', { name: T.mostrar })).not.toBeInTheDocument()
  })
})

describe('sin diálogos del navegador', () => {
  it('ninguna acción llama a confirm, alert ni prompt', async () => {
    api.quitarMiembro.mockResolvedValue({})
    renderDetalle()
    const fila = (await screen.findByText('lec@x.com')).closest('li')
    fireEvent.click(within(fila).getByRole('button', { name: T.quitar }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: T.quitar }))
    await waitFor(() => expect(api.quitarMiembro).toHaveBeenCalled())
    espias.forEach((e) => expect(e).not.toHaveBeenCalled())
  })
})
