import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Observación de Fernando (2026-09-20): Memoria estaba ruteada FUERA del
// caparazón de administración (/memoria, App.jsx), colgando como hermana de
// /admin/*. Un enlace guardado de esa URL no puede morir en un 404 mudo --
// App.jsx redirige a /admin/memoria, donde vive ahora (Admin.jsx).
//
// BrowserRouter real (no MemoryRouter): App.jsx arma el suyo propio y no
// recibe el router como prop, así que la URL se empuja con pushState antes
// de montar, como haría un enlace guardado del navegador.
// Store REAL (setState), no mockeado: mockear el módulo entero rompe otros
// named exports que se importan a la vez por el árbol de Dashboard
// (FACET_TOKENS en CenterPanel/Message.jsx, el .subscribe de
// useEjecutor.js) -- medido: TypeError "useJaxStore.subscribe is not a
// function". Con api/client mockeado a promesas que nunca resuelven,
// restoreSession() se queda en vuelo y nunca pisa el estado que fija el test
// (mismo truco que Admin.test.jsx).
import { useJaxStore } from './store/useJaxStore'

vi.mock('./apariencia/sincronizarApariencia', () => ({
  sincronizarApariencia: vi.fn(() => Promise.resolve()),
}))
// Sólo hace falta que las llamadas de red no resuelvan durante el test
// (mismo patrón que Admin.test.jsx): no importa la data, sólo el ruteo.
vi.mock('./api/client', () => ({
  default: {
    get: vi.fn(() => new Promise(() => {})),
    post: vi.fn(() => new Promise(() => {})),
  },
}))

import App from './App'
import { I18nProvider } from './i18n/index.jsx'

function renderApp() {
  return render(<I18nProvider><App /></I18nProvider>)
}

beforeEach(() => {
  localStorage.clear()
  useJaxStore.setState({
    token: 't',
    user: { user_id: 1, email: 'fernando@rich-hn.com', role: 'superadmin' },
    sessionRestoring: false,
    toasts: [],
  })
  window.history.pushState({}, '', '/')
})

describe('App -- /memoria (ruta vieja de Memoria, 2026-09-20)', () => {
  it('redirige a /admin/memoria, dentro del caparazón de Administración', async () => {
    window.history.pushState({}, '', '/memoria')
    renderApp()
    expect(await screen.findByRole('heading', { name: 'Memoria' })).toBeInTheDocument()
    expect(screen.getByText('Administración')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/admin/memoria')
  })

  it('desde /admin/memoria se llega a otra pantalla de Administración sin pasar por el inicio', async () => {
    window.history.pushState({}, '', '/admin/memoria')
    renderApp()
    await screen.findByRole('heading', { name: 'Memoria' })
    const enlaceDashboard = screen.getByRole('link', { name: /Dashboard/ })
    expect(enlaceDashboard).toHaveAttribute('href', '/admin/dashboard')
    fireEvent.click(enlaceDashboard)
    expect(window.location.pathname).toBe('/admin/dashboard')
  })
})
