import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom'

// Reset de contraseña (2026-09-12): el backend responde un código estable en
// `detail` y el texto lo pone i18n. Antes se mostraba el `detail` tal cual --
// en español aunque la UI estuviera en inglés. Y bcrypt no admite más de 72
// BYTES: el backend respondía 500 con una contraseña larga.
vi.mock('../api/client', () => ({ default: { post: vi.fn() } }))

import api from '../api/client'
import ResetPassword from './ResetPassword'
import { I18nProvider } from '../i18n/index.jsx'

function renderReset() {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={['/reset-password?token=tok-123']}>
        <ResetPassword />
      </MemoryRouter>
    </I18nProvider>
  )
}

function enviar(container, password) {
  const [nueva, confirmar] = container.querySelectorAll('input[type="password"]')
  fireEvent.change(nueva, { target: { value: password } })
  fireEvent.change(confirmar, { target: { value: password } })
  fireEvent.click(screen.getByText(/Cambiar contraseña/i))
}

beforeEach(() => {
  api.post.mockReset()
  localStorage.clear()
})

describe('ResetPassword', () => {
  it('traduce el código del backend en vez de mostrarlo crudo', async () => {
    api.post.mockRejectedValue({ response: { status: 400, data: { detail: 'reset_token_expirado' } } })
    const { container } = renderReset()
    enviar(container, 'una-clave-valida')
    await waitFor(() => expect(screen.getByText(/El enlace expiró/i)).toBeInTheDocument())
    expect(screen.queryByText('reset_token_expirado')).not.toBeInTheDocument()
  })

  it('un detail desconocido no se muestra tal cual: cae en el mensaje genérico', async () => {
    api.post.mockRejectedValue({ response: { status: 400, data: { detail: 'texto crudo del servidor' } } })
    const { container } = renderReset()
    enviar(container, 'una-clave-valida')
    await waitFor(() => expect(screen.getByText(/El enlace es inválido o ya fue usado/i)).toBeInTheDocument())
    expect(screen.queryByText('texto crudo del servidor')).not.toBeInTheDocument()
  })

  it('más de 72 bytes se frena antes de llamar al backend (cuenta bytes, no caracteres)', async () => {
    const { container } = renderReset()
    enviar(container, 'ñ'.repeat(40)) // 40 caracteres, 80 bytes
    await waitFor(() => expect(screen.getByText(/demasiado larga/i)).toBeInTheDocument())
    expect(api.post).not.toHaveBeenCalled()
  })
})
