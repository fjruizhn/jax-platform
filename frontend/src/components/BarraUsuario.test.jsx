import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'

// Barra superior derecha con íconos (2026-09-12, pedido de Fernando):
//   Usuario: <correo>  │  🌐 ES   ☀   ⚙ (solo superadmin)   ⏻
// Antes: ES/EN, ☀, el correo y "Salir" como texto suelto, y el enlace "Admin"
// al lado del logotipo.
const logoutMock = vi.fn()
let usuario = { email: 'fruiztorres@me.com', role: 'superadmin' }
let saliendo = null
vi.mock('../store/useJaxStore', () => ({
  useJaxStore: (selector) => selector({ user: usuario, logout: logoutMock, cambiarMiPassword: vi.fn(), saliendo }),
}))

// Contador de memoria sin verificar (2026-09-20, restricción dura): sólo
// superadmin lo pide, así que hay que mockear api/client acá o los tests de
// arriba (que no lo mencionan) le pegarían a axios de verdad.
vi.mock('../api/client', () => ({ default: { get: vi.fn() } }))

import BarraUsuario from './BarraUsuario'
import { I18nProvider } from '../i18n/index.jsx'
import { useTema } from '../store/useTema'
import { aplicarTema } from '../tema/aplicarTema'
import api from '../api/client'

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
  saliendo = null
  api.get.mockReset()
  api.get.mockResolvedValue({ data: { total: 0 } })
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

  // Task 9 (2026-09-18, historial-y-arreglos-de-pipeline): a diferencia del
  // engranaje de administración, el historial de pipelines lo ve CUALQUIER
  // usuario logueado, no sólo superadmin -- es su propio trabajo el que
  // quiere volver a ver, no una pantalla de administración.
  it('el ícono de historial lleva a /historial y lo ve cualquier rol', () => {
    const { unmount } = renderBarra()
    expect(screen.getByRole('link', { name: 'Historial de pipelines' })).toHaveAttribute('href', '/historial')
    unmount()
    usuario = { email: 'otro@example.com', role: 'operator' }
    renderBarra()
    expect(screen.getByRole('link', { name: 'Historial de pipelines' })).toHaveAttribute('href', '/historial')
  })

  it('salir es un ícono con nombre accesible y cierra la sesión', () => {
    renderBarra()
    fireEvent.click(screen.getByRole('button', { name: 'Salir' }))
    expect(logoutMock).toHaveBeenCalledTimes(1)
  })
})

describe('BarraUsuario — Mi cuenta', () => {
  it('un clic en el correo abre "Mi cuenta"', () => {
    renderBarra()
    fireEvent.click(screen.getByRole('button', { name: /fruiztorres@me.com/ }))
    expect(screen.getByRole('dialog', { name: 'Mi cuenta' })).toBeInTheDocument()
  })
})

// Fix round 1 (review de dd47d82): mientras el POST /auth/logout está en
// vuelo, el botón queda deshabilitado y ocupado (un doble clic no envía dos).
describe('BarraUsuario -- salir en vuelo', () => {
  it('con el logout en vuelo, ⏻ está deshabilitado y aria-busy', () => {
    saliendo = new Promise(() => {})
    renderBarra()
    const salir = screen.getByRole('button', { name: 'Salir' })
    expect(salir).toBeDisabled()
    expect(salir).toHaveAttribute('aria-busy', 'true')
  })
})

// Contador 🧩 N (2026-09-20, restricción dura): sólo superadmin (es quien
// puede actuar y el único que entra a /admin/memoria), oculto en 0 (para que
// cuando aparece signifique algo), y con el MISMO dato que la pantalla de
// Memoria (GET /admin/memoria/hechos?verificado=false&limite=1, que ya
// aplica el filtro correcto en el servidor -- SQL_CONTAR).
describe('BarraUsuario — memoria sin verificar', () => {
  it('superadmin pide el dato correcto (verificado=false, el filtro real)', async () => {
    renderBarra()
    await waitFor(() => expect(api.get).toHaveBeenCalledWith(
      '/admin/memoria/hechos', { params: { verificado: false, limite: 1 } }))
  })

  it('con N>0 muestra el contador con nombre accesible y lleva a /admin/memoria', async () => {
    api.get.mockResolvedValue({ data: { total: 3 } })
    renderBarra()
    const link = await screen.findByRole('link', { name: '3 hechos de memoria sin verificar' })
    expect(link).toHaveAttribute('href', '/admin/memoria')
    expect(link).toHaveTextContent('3')
  })

  it('en 0 el contador no se muestra -- para que cuando aparece signifique algo', async () => {
    api.get.mockResolvedValue({ data: { total: 0 } })
    renderBarra()
    await waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(screen.queryByRole('link', { name: /hechos de memoria sin verificar/ })).not.toBeInTheDocument()
  })

  it('un operator no ve el contador ni pide el dato (el endpoint es sólo de superadmin)', async () => {
    usuario = { email: 'otro@example.com', role: 'operator' }
    renderBarra()
    expect(screen.queryByRole('link', { name: /hechos de memoria sin verificar/ })).not.toBeInTheDocument()
    expect(api.get).not.toHaveBeenCalledWith('/admin/memoria/hechos', expect.anything())
  })
})
