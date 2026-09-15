import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'
import FijarPasswordModal from './FijarPasswordModal'
import { I18nProvider } from '../../i18n/index.jsx'

// Fijar la contraseña de otro usuario (2026-09-15, decisiones de Fernando que
// revierten U2). La regla es la única (lib/reglasPassword.js = backend).
beforeEach(() => {
  localStorage.clear()
})

function llenar(nueva, confirmar) {
  const onFijar = vi.fn().mockResolvedValue()
  render(<I18nProvider><FijarPasswordModal usuario={{ user_id: 2, email: 'b@x.io' }} onFijar={onFijar} onCerrar={vi.fn()} /></I18nProvider>)
  fireEvent.change(screen.getByLabelText('Nueva contraseña'), { target: { value: nueva } })
  fireEvent.change(screen.getByLabelText('Confirmar contraseña'), { target: { value: confirmar } })
  fireEvent.click(screen.getByRole('button', { name: 'Fijar contraseña' }))
  return onFijar
}

describe('FijarPasswordModal', () => {
  it('es un diálogo con el correo y avisa que el usuario tendrá que cambiarla', () => {
    llenar('', '')
    expect(screen.getByRole('dialog', { name: 'Fijar la contraseña de b@x.io' })).toBeInTheDocument()
    expect(screen.getByText(/tendrá que cambiarla/)).toBeInTheDocument()
  })

  it('la regla y la confirmación se frenan antes del backend', () => {
    const onFijar = llenar('corta', 'corta')
    expect(screen.getByText('Mínimo 8 caracteres')).toBeInTheDocument()
    expect(onFijar).not.toHaveBeenCalled()
  })

  it('válida y confirmada, llama a onFijar con la contraseña', async () => {
    const onFijar = llenar('clave-fijada-789', 'clave-fijada-789')
    await waitFor(() => expect(onFijar).toHaveBeenCalledWith('clave-fijada-789'))
  })
})
