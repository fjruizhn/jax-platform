import { render, screen } from '@testing-library/react'
import { describe, it, expect, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'
import AdminSidebar from './AdminSidebar'
import { I18nProvider } from '../../i18n/index.jsx'
import pkg from '../../../package.json'
import es from '../../i18n/es.js'
import { useApariencia } from '../../store/useApariencia'

function renderSidebar() {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <AdminSidebar />
      </MemoryRouter>
    </I18nProvider>
  )
}

beforeEach(() => {
  localStorage.clear()
  useApariencia.setState({ systemName: null, langDefault: null })
})

// I-1 (revisión final PR 2, 2026-09-14): "Axioma v0.3" estaba fijo en el
// JSX. Ahora sale de package.json vía __APP_VERSION__ (define en
// vite.config.js), así que el número en pantalla tiene que coincidir con
// el version de package.json -- una sola fuente. Guarda que ya pasaba;
// sigue vigente porque sin system_name configurado el nombre por defecto es
// la marca de i18n ("Axioma"), igual que antes del frente C.
describe('AdminSidebar -- versión (I-1)', () => {
  it('muestra la versión de package.json, no un número fijo en el JSX', () => {
    renderSidebar()
    expect(screen.getByText(`Axioma v${pkg.version}`)).toBeInTheDocument()
    expect(screen.queryByText('Axioma v0.3')).not.toBeInTheDocument()
  })
})

// system_name (frente C, 2026-09-16): la cabecera y el enlace de vuelta
// llevan el nombre del sistema en vez de "Axioma" fijo.
describe('AdminSidebar', () => {
  it('la cabecera y el enlace de vuelta usan el nombre del sistema', () => {
    useApariencia.setState({ systemName: 'Hal' })
    render(<I18nProvider><MemoryRouter><AdminSidebar /></MemoryRouter></I18nProvider>)
    expect(screen.getByText(`Hal v${__APP_VERSION__}`)).toBeInTheDocument()
    expect(screen.getByText(es.adminBack('Hal'))).toBeInTheDocument()
  })
})

// Observación de Fernando (2026-09-20): Memoria tiene que ser el 2º ítem del
// menú. Memoria.jsx quedó ruteada como hermana de /admin/* con la marca
// `absoluto: true` -- de ahí salían las dos quejas (ver App.test.jsx y
// Admin.test.jsx). Acá se ata el orden; el candado de ruta relativa
// (sin `absoluto`) se ata abajo.
describe('AdminSidebar -- posición de Memoria (2026-09-20)', () => {
  it('Memoria es el 2º ítem del menú, justo después de Dashboard', () => {
    renderSidebar()
    // Cada enlace lleva un ícono antes del texto (span aparte); el texto
    // visible es lo que importa para el orden, no el ícono.
    const enlaces = screen.getAllByRole('link').map((a) => a.textContent)
    // El primer enlace de la lista es "Dashboard"; el 2º tiene que ser
    // "Memoria" -- no el último, como estaba antes.
    expect(enlaces[0]).toContain(es.adminDashboard)
    expect(enlaces[1]).toContain(es.adminMemoria)
  })

  it('Memoria es una ruta relativa del caparazón (/admin/memoria), no absoluta', () => {
    renderSidebar()
    const enlaceMemoria = screen.getByRole('link', { name: new RegExp(es.adminMemoria) })
    expect(enlaceMemoria).toHaveAttribute('href', '/admin/memoria')
  })
})
