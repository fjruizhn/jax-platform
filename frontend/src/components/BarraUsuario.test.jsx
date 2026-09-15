import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'

// Barra superior derecha con íconos (2026-09-12, pedido de Fernando):
//   Usuario: <correo>  │  🌐 ES   ☀   ⚙ (solo superadmin)   ⏻
// Antes: ES/EN, ☀, el correo y "Salir" como texto suelto, y el enlace "Admin"
// al lado del logotipo.
const logoutMock = vi.fn()
let usuario = { email: 'fruiztorres@me.com', role: 'superadmin' }
vi.mock('../store/useJaxStore', () => ({
  useJaxStore: (selector) => selector({ user: usuario, logout: logoutMock }),
}))

import BarraUsuario from './BarraUsuario'
import { I18nProvider } from '../i18n/index.jsx'
import { useTema } from '../store/useTema'
import { aplicarTema } from '../tema/aplicarTema'

function renderBarra() {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <BarraUsuario />
      </MemoryRouter>
    </I18nProvider>
  )
}

beforeEach(() => {
  logoutMock.mockReset()
  localStorage.clear()
  // El store de tema es un módulo único: un test que toca el interruptor no
  // puede dejarle el tema cambiado al siguiente.
  useTema.setState({ theme: 'dark', predeterminado: null })
  aplicarTema('dark')
  usuario = { email: 'fruiztorres@me.com', role: 'superadmin' }
})

describe('BarraUsuario', () => {
  it('muestra "Usuario:" y el correo de la sesión', () => {
    renderBarra()
    expect(screen.getByText(/Usuario:/)).toBeInTheDocument()
    expect(screen.getByText('fruiztorres@me.com')).toBeInTheDocument()
  })

  it('idioma: muestra la sigla actual y un clic cambia al otro', () => {
    renderBarra()
    const boton = screen.getByRole('button', { name: 'Cambiar idioma a English' })
    expect(boton).toHaveTextContent('ES')
    fireEvent.click(boton)
    expect(screen.getByRole('button', { name: 'Switch language to Español' })).toHaveTextContent('EN')
  })

  it('tema: el botón dice a qué modo pasa y alterna', () => {
    renderBarra()
    fireEvent.click(screen.getByRole('button', { name: 'Modo claro' }))
    expect(screen.getByRole('button', { name: 'Modo oscuro' })).toBeInTheDocument()
  })

  it('el engranaje de administración lleva a /admin y solo lo ve el superadmin', () => {
    const { unmount } = renderBarra()
    expect(screen.getByRole('link', { name: 'Administración' })).toHaveAttribute('href', '/admin')
    unmount()
    usuario = { email: 'otro@example.com', role: 'operator' }
    renderBarra()
    expect(screen.queryByRole('link', { name: 'Administración' })).not.toBeInTheDocument()
  })

  it('salir es un ícono con nombre accesible y cierra la sesión', () => {
    renderBarra()
    fireEvent.click(screen.getByRole('button', { name: 'Salir' }))
    expect(logoutMock).toHaveBeenCalledTimes(1)
  })
})
