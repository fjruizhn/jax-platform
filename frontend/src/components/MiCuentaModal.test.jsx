import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Mi cuenta (2026-09-12, etapa 4): cambiar la propia contraseña exige la
// actual; la regla es la misma del backend y los errores se traducen.
const cambiarMock = vi.fn()
vi.mock('../store/useJaxStore', () => ({
  useJaxStore: (selector) => selector({ cambiarMiPassword: cambiarMock }),
}))

import MiCuentaModal from './MiCuentaModal'
import { I18nProvider } from '../i18n/index.jsx'

function llenar(actual, nueva, confirmar) {
  render(<I18nProvider><MiCuentaModal onCerrar={vi.fn()} /></I18nProvider>)
  fireEvent.change(screen.getByLabelText('Contraseña actual'), { target: { value: actual } })
  fireEvent.change(screen.getByLabelText('Nueva contraseña'), { target: { value: nueva } })
  fireEvent.change(screen.getByLabelText('Confirmar contraseña'), { target: { value: confirmar } })
  fireEvent.click(screen.getByRole('button', { name: 'Cambiar contraseña' }))
}

beforeEach(() => {
  cambiarMock.mockReset()
  localStorage.clear()
})

describe('MiCuentaModal', () => {
  it('si la confirmación no coincide no llama al backend', () => {
    llenar('vieja-clave', 'nueva-clave-9', 'otra-clave-9')
    expect(screen.getByText('Las contraseñas no coinciden')).toBeInTheDocument()
    expect(cambiarMock).not.toHaveBeenCalled()
  })

  it('más de 72 bytes se frena antes de llamar al backend', () => {
    llenar('vieja-clave', 'ñ'.repeat(40), 'ñ'.repeat(40))
    expect(screen.getByText(/demasiado larga/)).toBeInTheDocument()
    expect(cambiarMock).not.toHaveBeenCalled()
  })

  it('la contraseña actual incorrecta se muestra traducida', async () => {
    cambiarMock.mockRejectedValue({ response: { status: 400, data: { detail: 'password_actual_incorrecta' } } })
    llenar('equivocada', 'nueva-clave-9', 'nueva-clave-9')
    expect(await screen.findByText('La contraseña actual no es correcta.')).toBeInTheDocument()
  })

  it('con éxito avisa que las otras sesiones se cerraron', async () => {
    cambiarMock.mockResolvedValue()
    llenar('vieja-clave', 'nueva-clave-9', 'nueva-clave-9')
    expect(await screen.findByText('Contraseña cambiada. Tus otras sesiones se cerraron.')).toBeInTheDocument()
    expect(cambiarMock).toHaveBeenCalledWith('vieja-clave', 'nueva-clave-9')
  })

  // Fix round 1 (2026-09-15, Ruling U24): Escape cierra los tres modales de
  // usuarios. Nunca un clic en el fondo -- así no se pierde lo escrito.
  // Ruling U27 (review final, 2026-09-15): MiCuentaModal usa Dialogo -- es un
  // diálogo nombrado por su título, vive fuera de #root y deja la app (barra
  // de usuario, dashboard) inert detrás (minor 4: antes el fondo seguía vivo).
  it('es un diálogo nombrado por su título, fuera de #root, con #root inert', () => {
    const root = document.createElement('div')
    root.id = 'root'
    document.body.appendChild(root)
    try {
      render(<I18nProvider><MiCuentaModal onCerrar={vi.fn()} /></I18nProvider>, { container: root })
      const dialogo = screen.getByRole('dialog', { name: 'Mi cuenta' })
      expect(root.contains(dialogo)).toBe(false)
      expect(root).toHaveAttribute('inert')
      expect(screen.getByLabelText('Contraseña actual')).toHaveFocus()
    } finally {
      root.remove()
    }
  })

  it('Escape cierra el modal', () => {
    const onCerrar = vi.fn()
    render(<I18nProvider><MiCuentaModal onCerrar={onCerrar} /></I18nProvider>)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onCerrar).toHaveBeenCalledTimes(1)
  })
})
