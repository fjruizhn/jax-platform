import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'

// Límite de intentos de login (2026-09-12): el backend responde 429 con
// Retry-After. Antes ese caso caía en "Usuario o contraseña incorrectos", que
// es falso: la persona no se equivocó, tiene que esperar.
const loginMock = vi.fn()
const clearAvisoSesionMock = vi.fn()
// Motivo del cierre de sesión (2026-09-14, Task 4b): mutable para que cada
// test pueda simular que se llegó a Login con un aviso ya puesto (p.ej. el
// interceptor de api/client.js lo dejó tras un refresh fallido).
let avisoSesion = null
vi.mock('../store/useJaxStore', () => ({
  useJaxStore: (selector) => selector({ login: loginMock, avisoSesion, clearAvisoSesion: clearAvisoSesionMock }),
}))
vi.mock('../api/client', () => ({ default: { post: vi.fn() } }))

import api from '../api/client'
import Login from './Login'
import { I18nProvider } from '../i18n/index.jsx'

function renderLogin() {
  return render(
    <I18nProvider>
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    </I18nProvider>
  )
}

function enviar() {
  fireEvent.change(screen.getByPlaceholderText('nombre@empresa.com'), { target: { value: 'a@b.c' } })
  fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'x' } })
  fireEvent.click(screen.getByText(/Entrar a Axioma/i))
}

beforeEach(() => {
  loginMock.mockReset()
  clearAvisoSesionMock.mockReset()
  avisoSesion = null
  localStorage.clear()
})

describe('Login -- límite de intentos', () => {
  it('un 429 dice cuánto esperar, no "credenciales incorrectas"', async () => {
    loginMock.mockRejectedValue({ response: { status: 429, headers: { 'retry-after': '42' }, data: { detail: 'x' } } })
    renderLogin()
    enviar()
    await waitFor(() => expect(screen.getByText(/Vuelve a intentarlo en 42 segundo/i)).toBeInTheDocument())
    expect(screen.queryByText(/Usuario o contraseña incorrectos/i)).not.toBeInTheDocument()
  })

  it('un 429 sin Retry-After usa el mensaje genérico de espera', async () => {
    loginMock.mockRejectedValue({ response: { status: 429, headers: {}, data: {} } })
    renderLogin()
    enviar()
    await waitFor(() => expect(screen.getByText(/Demasiados intentos\. Espera/i)).toBeInTheDocument())
  })

  it('un 401 sigue diciendo credenciales incorrectas', async () => {
    loginMock.mockRejectedValue({ response: { status: 401, headers: {}, data: {} } })
    renderLogin()
    enviar()
    await waitFor(() => expect(screen.getByText(/Usuario o contraseña incorrectos/i)).toBeInTheDocument())
  })
})

// Aviso de por qué se cerró la sesión (2026-09-14, Task 4b): antes el
// interceptor de api/client.js borraba la sesión en silencio ante un refresh
// fallido. Ahora deja el motivo en avisoSesion (store) y Login lo muestra.
describe('Login -- aviso de cierre de sesión', () => {
  it('con avisoSesion puesto, muestra el mensaje traducido en un role="alert"', () => {
    avisoSesion = 'sesion_invalida'
    renderLogin()
    const alerta = screen.getByRole('alert')
    expect(alerta).toHaveTextContent(/Tu sesión se cerró: tu usuario fue desactivado/)
  })

  it('sin avisoSesion, no muestra ningún role="alert"', () => {
    renderLogin()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('un login exitoso borra el aviso', async () => {
    avisoSesion = 'sesion_invalida'
    loginMock.mockResolvedValue({ access_token: 'x' })
    renderLogin()
    enviar()
    await waitFor(() => expect(clearAvisoSesionMock).toHaveBeenCalled())
  })

  // I-1 (revisión final, 2026-09-14): antes clearAvisoSesion() sólo corría
  // DESPUÉS de un login exitoso -- si quedaba un aviso viejo y esta persona
  // escribía mal la contraseña, el aviso ("Tu sesión venció") seguía en
  // pantalla junto con "Usuario o contraseña incorrectos": dos cajas rojas.
  // Ahora se limpia al ENVIAR el formulario, no al tener éxito.
  it('un login fallido (contraseña equivocada) también borra el aviso previo', async () => {
    avisoSesion = 'sesion_invalida'
    loginMock.mockRejectedValue({ response: { status: 401, headers: {}, data: {} } })
    renderLogin()
    enviar()
    await waitFor(() => expect(screen.getByText(/Usuario o contraseña incorrectos/i)).toBeInTheDocument())
    expect(clearAvisoSesionMock).toHaveBeenCalled()
  })

  it('las claves sesion_invalida y sesion_expirada existen en es y en', async () => {
    const { default: es } = await import('../i18n/es.js')
    const { default: en } = await import('../i18n/en.js')
    for (const claves of [es, en]) {
      expect(typeof claves.sesion_invalida).toBe('string')
      expect(claves.sesion_invalida.length).toBeGreaterThan(0)
      expect(typeof claves.sesion_expirada).toBe('string')
      expect(claves.sesion_expirada.length).toBeGreaterThan(0)
    }
  })
})

// Ojo HAL animado (2026-09-14, fix vivo del PR 1, Ruling 21): antes un SVG
// fijo de 80px; ahora HalEye en modo `reposo` -- el estado de reposo del
// panel (azul, pulse-slow), fijo, sin depender de la store (acá no hay
// sesión: useJaxStore está mockeado sin facets/lasManos/etc).
describe('Login -- ojo HAL', () => {
  it('muestra el HalEye en estado de reposo fijo (pulse-slow), no el apagado de "sin sesión"', () => {
    const { container } = renderLogin()
    expect(container.querySelector('.hal-anim-pulse-slow')).toBeInTheDocument()
    expect(container.querySelector('.hal-anim-none')).not.toBeInTheDocument()
    expect(screen.queryByText(/LAS MANOS DOWN/i)).not.toBeInTheDocument()
  })
})

// Recuperación de contraseña (2026-09-12): comparte el límite del login. Con un
// 429 no se procesó nada, así que "si el correo existe, te llegará" es falso.
describe('Login -- recuperación de contraseña', () => {
  function pedirRecuperacion() {
    fireEvent.click(screen.getByText(/¿Olvidaste tu contraseña\?/i))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'a@b.c' } })
    fireEvent.click(screen.getByText(/Enviar instrucciones/i))
  }

  it('un 429 dice cuánto esperar y no dice que el correo llegará', async () => {
    api.post.mockRejectedValue({ response: { status: 429, headers: { 'retry-after': '30' }, data: {} } })
    renderLogin()
    pedirRecuperacion()
    await waitFor(() => expect(screen.getByText(/Vuelve a intentarlo en 30 segundo/i)).toBeInTheDocument())
    expect(screen.queryByText(/Si el correo existe/i)).not.toBeInTheDocument()
  })

  it('otro error sigue mostrando el mensaje neutro (no revela si la cuenta existe)', async () => {
    api.post.mockRejectedValue({ response: { status: 500, headers: {}, data: {} } })
    renderLogin()
    pedirRecuperacion()
    await waitFor(() => expect(screen.getByText(/Si el correo existe/i)).toBeInTheDocument())
  })
})

// Placeholder del email genérico (2026-09-14, decisión de Fernando, Task 9):
// antes era un correo real hardcodeado ("fernando@rich-hn.com"), que
// filtraba un dato personal en el HTML servido a cualquier visitante.
describe('Login -- placeholder del email', () => {
  // M-3 (revisión final, 2026-09-14): sólo probar en es no prueba el origen
  // i18n -- un placeholder="nombre@empresa.com" hardcodeado en Login.jsx
  // pasaría igual. Se renderiza también en en y se exige en.emailPlaceholder
  // (name@company.com); si es y en dieran el mismo valor, el render en en no
  // probaría nada, así que también se exige que difieran.
  it('el placeholder del email sale de i18n (es y en), no del correo real de Fernando', async () => {
    const { default: es } = await import('../i18n/es.js')
    const { default: en } = await import('../i18n/en.js')
    expect(es.emailPlaceholder).not.toBe(en.emailPlaceholder)

    const primero = renderLogin()
    expect(screen.getByPlaceholderText(es.emailPlaceholder)).toBeInTheDocument()
    expect(es.emailPlaceholder).not.toContain('rich-hn')
    primero.unmount()

    localStorage.setItem('jax_lang', 'en')
    renderLogin()
    expect(screen.getByPlaceholderText(en.emailPlaceholder)).toBeInTheDocument()
    expect(en.emailPlaceholder).toBe('name@company.com')
  })
})
