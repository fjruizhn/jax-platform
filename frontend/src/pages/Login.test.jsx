import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'

// Límite de intentos de login (2026-09-12): el backend responde 429 con
// Retry-After. Antes ese caso caía en "Usuario o contraseña incorrectos", que
// es falso: la persona no se equivocó, tiene que esperar.
const loginMock = vi.fn()
vi.mock('../store/useJaxStore', () => ({
  useJaxStore: (selector) => selector({ login: loginMock }),
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
  fireEvent.change(screen.getByPlaceholderText('fernando@rich-hn.com'), { target: { value: 'a@b.c' } })
  fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'x' } })
  fireEvent.click(screen.getByText(/Entrar a Axioma/i))
}

beforeEach(() => {
  loginMock.mockReset()
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
