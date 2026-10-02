import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'
import '@testing-library/jest-dom'

// Proyectos dentro del caparazon de Administracion (E1.3, Fernando 2026-10-02):
// /admin/proyectos y /admin/proyectos/:id montan las MISMAS pantallas que
// /proyectos, detectando el contexto por la ruta.
vi.mock('../api/client', () => ({ default: { get: vi.fn(() => new Promise(() => {})) } }))
vi.mock('../api/proyectos', () => ({
  listarProyectos: vi.fn(),
  crearProyecto: vi.fn(),
  verProyecto: vi.fn(),
  listarMiembros: vi.fn(),
}))

import * as api from '../api/proyectos'
import Admin from './Admin'
import Proyectos from './Proyectos'
import ProyectoDetalle from './ProyectoDetalle'
import { I18nProvider } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
import es from '../i18n/es.js'

const T = es.proyectos
const INICIAL = useJaxStore.getState()
const P1 = { id: 7, uuid: 'u7', nombre: 'Alfa', descripcion: null, estado: 'ACTIVE', papel: 'OWNER' }

function Ruta() {
  return <div data-testid="ruta">{useLocation().pathname}</div>
}

function renderAdmin(ruta) {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={[ruta]}>
        <Ruta />
        <Routes>
          <Route path="/admin/*" element={<Admin />} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  )
}

function renderSuelta(ruta) {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={[ruta]}>
        <Ruta />
        <Routes>
          <Route path="/proyectos" element={<Proyectos />} />
          <Route path="/proyectos/:id" element={<ProyectoDetalle />} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  )
}

const itemMenu = () => screen.getByRole('link', { name: new RegExp(es.adminProyectos) })

beforeEach(() => {
  localStorage.clear()
  useJaxStore.setState({ ...INICIAL, token: 't', user: { user_id: 1, email: 'a@b.c', role: 'superadmin' } }, true)
  Object.values(api).forEach((f) => f.mockReset())
  api.listarProyectos.mockResolvedValue({ proyectos: [P1], siguiente: null })
  api.verProyecto.mockResolvedValue(P1)
  api.listarMiembros.mockResolvedValue([])
})

describe('/admin/proyectos', () => {
  it('se muestra dentro del caparazon, con Proyectos activo y un solo «Volver a Axioma»', async () => {
    renderAdmin('/admin/proyectos')
    expect(await screen.findByRole('heading', { name: T.titulo })).toBeInTheDocument()
    expect(screen.getByRole('main')).toContainElement(screen.getByRole('heading', { name: T.titulo }))
    expect(itemMenu().className).toMatch(/bg-acento-fondo/)
    // el del menu si; el propio de la pantalla no
    expect(screen.getAllByText(es.adminBack('Axioma'))).toHaveLength(1)
    // sin envoltorio de pagina completa
    expect(screen.getByRole('main').querySelector('.min-h-dvh')).toBeNull()
  })

  it('los enlaces de la lista usan /admin/proyectos', async () => {
    renderAdmin('/admin/proyectos')
    const enlace = await screen.findByRole('link', { name: /Alfa/ })
    expect(enlace).toHaveAttribute('href', '/admin/proyectos/7')
  })

  it('crear un proyecto navega a /admin/proyectos/{id}', async () => {
    api.crearProyecto.mockResolvedValue({ id: 9, nombre: 'Nuevo' })
    renderAdmin('/admin/proyectos')
    await screen.findByRole('heading', { name: T.titulo })
    fireEvent.click(screen.getByRole('button', { name: T.nuevo }))
    const dlg = screen.getByRole('dialog')
    fireEvent.change(within(dlg).getByRole('textbox', { name: new RegExp(T.nombre) }), { target: { value: 'Nuevo' } })
    fireEvent.submit(dlg.querySelector('form'))
    await waitFor(() => expect(screen.getByTestId('ruta')).toHaveTextContent('/admin/proyectos/9'))
  })
})

describe('/admin/proyectos/:id', () => {
  it('deja Proyectos activo, sin «Volver a Axioma» propio y vuelve a /admin/proyectos', async () => {
    renderAdmin('/admin/proyectos/7')
    expect(await screen.findByRole('heading', { name: 'Alfa' })).toBeInTheDocument()
    expect(itemMenu().className).toMatch(/bg-acento-fondo/)
    expect(screen.getAllByText(es.adminBack('Axioma'))).toHaveLength(1)
    const volver = screen.getByRole('link', { name: new RegExp(T.volverAProyectos) })
    expect(volver).toHaveAttribute('href', '/admin/proyectos')
    expect(screen.getByRole('main').querySelector('.min-h-dvh')).toBeNull()
  })
})

describe('pantallas sueltas (sin cambios)', () => {
  it('/proyectos conserva su «Volver a Axioma» y enlaces a /proyectos', async () => {
    useJaxStore.setState({ ...INICIAL, token: 't', user: { user_id: 1, email: 'a@b.c', role: 'operator' } }, true)
    renderSuelta('/proyectos')
    const enlace = await screen.findByRole('link', { name: /Alfa/ })
    expect(enlace).toHaveAttribute('href', '/proyectos/7')
    expect(screen.getByText(es.historialBack('Axioma'))).toBeInTheDocument()
  })

  it('/proyectos/:id vuelve a /proyectos', async () => {
    renderSuelta('/proyectos/7')
    const volver = await screen.findByRole('link', { name: new RegExp(T.volverAProyectos) })
    expect(volver).toHaveAttribute('href', '/proyectos')
  })
})
