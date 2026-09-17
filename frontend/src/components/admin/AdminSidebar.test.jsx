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
