import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Mi cuenta (2026-09-12, etapa 4): cambiar la propia contraseña exige la
// actual; la regla es la misma del backend y los errores se traducen.
const cambiarMock = vi.fn()
// U34 (2026-09-15): el modo obligatorio usa logout ("Cerrar sesión") y
// addToast (aviso al terminar).
const logoutMock = vi.fn()
const addToastMock = vi.fn()
vi.mock('../store/useJaxStore', () => ({
  useJaxStore: (selector) => selector({ cambiarMiPassword: cambiarMock, logout: logoutMock, addToast: addToastMock }),
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
  logoutMock.mockReset()
  addToastMock.mockReset()
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

  // Fix round 1 del re-review final (2026-09-15): tras el éxito el form se
  // desmontaba y el foco caía a body; y la región role="status" se insertaba
  // ya con su texto (una región viva agregada junto con su contenido no se
  // anuncia de forma confiable). Ahora la región existe vacía desde el inicio
  // y el foco va al botón Cerrar del éxito.
  it('la región de estado existe vacía antes de enviar y recibe el texto de éxito', async () => {
    cambiarMock.mockResolvedValue()
    render(<I18nProvider><MiCuentaModal onCerrar={vi.fn()} /></I18nProvider>)
    const estado = screen.getByRole('status')
    expect(estado).toBeEmptyDOMElement()
    fireEvent.change(screen.getByLabelText('Contraseña actual'), { target: { value: 'vieja-clave' } })
    fireEvent.change(screen.getByLabelText('Nueva contraseña'), { target: { value: 'nueva-clave-9' } })
    fireEvent.change(screen.getByLabelText('Confirmar contraseña'), { target: { value: 'nueva-clave-9' } })
    fireEvent.click(screen.getByRole('button', { name: 'Cambiar contraseña' }))
    await waitFor(() => expect(estado).toHaveTextContent('Contraseña cambiada. Tus otras sesiones se cerraron.'))
    expect(screen.getByRole('status')).toBe(estado)
  })

  it('tras el éxito el foco va al botón Cerrar', async () => {
    cambiarMock.mockResolvedValue()
    llenar('vieja-clave', 'nueva-clave-9', 'nueva-clave-9')
    const cerrar = await screen.findByRole('button', { name: 'Cerrar' })
    await waitFor(() => expect(cerrar).toHaveFocus())
  })

  it('Escape cierra el modal', () => {
    const onCerrar = vi.fn()
    render(<I18nProvider><MiCuentaModal onCerrar={onCerrar} /></I18nProvider>)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onCerrar).toHaveBeenCalledTimes(1)
  })
})

describe('MiCuentaModal obligatorio (cambio exigido por el admin, U34)', () => {
  function renderObligatorio() {
    const onCerrar = vi.fn()
    render(<I18nProvider><MiCuentaModal obligatorio onCerrar={onCerrar} /></I18nProvider>)
    return onCerrar
  }

  function llenarObligatorio(valor) {
    fireEvent.change(screen.getByLabelText('Contraseña actual'), { target: { value: valor } })
    fireEvent.change(screen.getByLabelText('Nueva contraseña'), { target: { value: valor } })
    fireEvent.change(screen.getByLabelText('Confirmar contraseña'), { target: { value: valor } })
    fireEvent.click(screen.getByRole('button', { name: 'Cambiar contraseña' }))
  }

  it('no se puede cerrar: sin Cancelar y Escape no hace nada', () => {
    const onCerrar = renderObligatorio()
    expect(screen.getByRole('dialog', { name: 'Cambiá tu contraseña' })).toBeInTheDocument()
    expect(screen.getByText('Un administrador fijó tu contraseña. Para seguir, elegí una nueva.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Cancelar' })).not.toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onCerrar).not.toHaveBeenCalled()
  })

  it('"Cerrar sesión" termina la sesión (logout está permitido)', () => {
    renderObligatorio()
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar sesión' }))
    expect(logoutMock).toHaveBeenCalledTimes(1)
  })

  it('la misma contraseña que fijó el admin se rechaza traducida', async () => {
    cambiarMock.mockRejectedValue({ response: { status: 400, data: { detail: 'password_igual_a_la_actual' } } })
    renderObligatorio()
    llenarObligatorio('fijada-por-admin')
    expect(await screen.findByText('La nueva contraseña tiene que ser distinta de la que te dieron.')).toBeInTheDocument()
  })

  it('al terminar avisa con un toast y no muestra el bloque "Cerrar" (RequireAuth desmonta el diálogo)', async () => {
    cambiarMock.mockResolvedValue()
    renderObligatorio()
    llenarObligatorio('nueva-clave-propia-9')
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith({
      type: 'success', message: 'Contraseña cambiada. Ya podés seguir.',
    }))
    expect(screen.queryByRole('button', { name: 'Cerrar' })).not.toBeInTheDocument()
  })
})
