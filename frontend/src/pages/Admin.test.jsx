import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import '@testing-library/jest-dom'

// U34 (fix round 1 del review de dd47d82): tras el cambio obligatorio de
// contraseña, RequireAuth pone el foco en [data-foco-inicial]. En Admin es el
// <main> (enfocable con tabIndex -1). Sin este test, quitar el marcador dejaba
// la suite verde.
vi.mock('../api/client', () => ({ default: { get: vi.fn(() => new Promise(() => {})) } }))

import Admin from './Admin'
import { I18nProvider } from '../i18n/index.jsx'

beforeEach(() => {
  localStorage.clear()
})

describe('Admin', () => {
  it('el <main> es el punto de entrada del foco ([data-foco-inicial], enfocable)', () => {
    render(<I18nProvider><MemoryRouter initialEntries={['/admin/dashboard']}><Admin /></MemoryRouter></I18nProvider>)
    const main = screen.getByRole('main')
    expect(main).toHaveAttribute('data-foco-inicial')
    expect(main).toHaveAttribute('tabindex', '-1')
  })
})

// Observación de Fernando (2026-09-20): Memoria quedaba ruteada FUERA del
// caparazón (/memoria, hermana de /admin/*, App.jsx), así que no tenía
// sidebar y su única salida era "Volver a Axioma" -- al inicio, no a
// Administración. Ahora "memoria" es una ruta anidada más, igual que
// dashboard/keys/users/etc: la barra lateral queda SIEMPRE visible, y desde
// ahí se llega a cualquier otra pantalla de Administración sin pasar por "/".
// Admin.jsx usa rutas relativas ("memoria", "dashboard", ...) que se
// resuelven contra el prefijo del <Route> padre -- igual que en App.jsx real
// (path="/admin/*"). Sin ese padre, "memoria" no matchea "/admin/memoria"
// (medido: "No routes matched location").
function renderAdminBajoAdminStar(ruta) {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={[ruta]}>
        <Routes>
          <Route path="/admin/*" element={<Admin />} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>
  )
}

describe('Admin > ruta "memoria" (2026-09-20)', () => {
  it('vive dentro del caparazón: la barra lateral de Administración está presente junto a Memoria', async () => {
    renderAdminBajoAdminStar('/admin/memoria')
    expect(screen.getByText('Administración')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Memoria' })).toBeInTheDocument()
  })

  it('desde Memoria se llega a otra pantalla de Administración sin pasar por el inicio', async () => {
    renderAdminBajoAdminStar('/admin/memoria')
    await screen.findByRole('heading', { name: 'Memoria' })
    const enlaceDashboard = screen.getByRole('link', { name: /Dashboard/ })
    expect(enlaceDashboard).toHaveAttribute('href', '/admin/dashboard')
  })
})

// Fix round 1 (MINOR-7, 2026-09-22): "pipelines-ocultos" tenía ruta
// (Task 7) pero ninguna entrada en el menú -- un superadmin que llegaba
// desde el enlace de BarraUsuario no podía volver sin salir de
// Administración. Mismo patrón que el candado de Memoria de arriba.
describe('Admin > ruta "pipelines-ocultos" (fix round 1, MINOR-7)', () => {
  it('vive dentro del caparazón: la barra lateral está presente junto a Pipelines ocultos', async () => {
    renderAdminBajoAdminStar('/admin/pipelines-ocultos')
    expect(screen.getByText('Administración')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Pipelines ocultos' })).toBeInTheDocument()
  })

  it('el menú tiene una entrada propia que lleva a /admin/pipelines-ocultos', async () => {
    renderAdminBajoAdminStar('/admin/pipelines-ocultos')
    await screen.findByRole('heading', { name: 'Pipelines ocultos' })
    const enlace = screen.getByRole('link', { name: /Pipelines ocultos/ })
    expect(enlace).toHaveAttribute('href', '/admin/pipelines-ocultos')
  })
})
