import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'

// I-1 (revisión final PR 2, 2026-09-14): "Axioma v0.3" estaba fijo en el
// JSX. Ahora sale de package.json vía __APP_VERSION__ (define en
// vite.config.js), así que el número en pantalla tiene que coincidir con
// el version de package.json -- una sola fuente.
import AdminSidebar from './AdminSidebar'
import { I18nProvider } from '../../i18n/index.jsx'
import pkg from '../../../package.json'

function renderSidebar() {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <AdminSidebar />
      </MemoryRouter>
    </I18nProvider>
  )
}

describe('AdminSidebar -- versión (I-1)', () => {
  it('muestra la versión de package.json, no un número fijo en el JSX', () => {
    renderSidebar()
    expect(screen.getByText(`Axioma v${pkg.version}`)).toBeInTheDocument()
    expect(screen.queryByText('Axioma v0.3')).not.toBeInTheDocument()
  })
})
