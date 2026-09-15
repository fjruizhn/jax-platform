import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'
import CrearUsuarioModal from './CrearUsuarioModal'
import { I18nProvider } from '../../i18n/index.jsx'

// Ruling U27 (review final de la etapa 4, 2026-09-15; I2/U26a): el alta vivía
// en línea en AdminUsers sin role="dialog", sin aria-modal ni aria-labelledby,
// sin Escape, y sus tres campos sólo tenían placeholder (sin <label>). Ahora
// es un componente propio sobre Dialogo.
beforeEach(() => localStorage.clear())

function renderModal(props = {}) {
  const onCrear = props.onCrear || vi.fn().mockResolvedValue()
  const onCerrar = props.onCerrar || vi.fn()
  render(<I18nProvider><CrearUsuarioModal onCrear={onCrear} onCerrar={onCerrar} /></I18nProvider>, props.opciones)
  return { onCrear, onCerrar }
}

describe('CrearUsuarioModal', () => {
  it('es un diálogo nombrado por su título, fuera de #root, con #root inert y foco en el correo', () => {
    const root = document.createElement('div')
    root.id = 'root'
    document.body.appendChild(root)
    try {
      renderModal({ opciones: { container: root } })
      const dialogo = screen.getByRole('dialog', { name: 'Crear Usuario' })
      expect(dialogo).toHaveAttribute('aria-modal', 'true')
      expect(root.contains(dialogo)).toBe(false)
      expect(root).toHaveAttribute('inert')
      expect(screen.getByLabelText('Email')).toHaveFocus()
    } finally {
      root.remove()
    }
  })

  it('los tres campos tienen <label> real (no sólo placeholder)', () => {
    renderModal()
    expect(screen.getByLabelText('Email')).toHaveAttribute('type', 'email')
    expect(screen.getByLabelText('Rol').tagName).toBe('SELECT')
    expect(screen.getByLabelText('Contraseña temporal')).toHaveAttribute('type', 'password')
  })

  it('en inglés las etiquetas salen traducidas', () => {
    localStorage.setItem('jax_lang', 'en')
    renderModal()
    expect(screen.getByRole('dialog', { name: 'Create User' })).toBeInTheDocument()
    expect(screen.getByLabelText('Role')).toBeInTheDocument()
    expect(screen.getByLabelText('Temporary password')).toBeInTheDocument()
  })

  it('Escape cierra el modal', () => {
    const { onCerrar } = renderModal()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onCerrar).toHaveBeenCalledTimes(1)
  })

  it('crear manda email, rol y contraseña', async () => {
    const { onCrear } = renderModal()
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'n@x.io' } })
    fireEvent.change(screen.getByLabelText('Rol'), { target: { value: 'viewer' } })
    fireEvent.change(screen.getByLabelText('Contraseña temporal'), { target: { value: 'clave-larga-9' } })
    fireEvent.click(screen.getByRole('button', { name: 'Crear' }))
    await waitFor(() => expect(onCrear).toHaveBeenCalledWith({ email: 'n@x.io', role: 'viewer', password: 'clave-larga-9' }))
  })

  // Fix round 1 del re-review final (2026-09-15): mientras guarda, el botón
  // decía "Subiendo…" (t.attachUploading, el texto de adjuntar archivos).
  it.each([['es', 'Crear', 'Creando…'], ['en', 'Create', 'Creating…']])('en %s, mientras guarda el botón dice "%s" -> "%s"', async (lang, antes, durante) => {
    localStorage.setItem('jax_lang', lang)
    renderModal({ onCrear: vi.fn(() => new Promise(() => {})) })
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'n@x.io' } })
    fireEvent.change(screen.getByLabelText(lang === 'es' ? 'Contraseña temporal' : 'Temporary password'), { target: { value: 'clave-larga-9' } })
    fireEvent.click(screen.getByRole('button', { name: antes }))
    expect(await screen.findByRole('button', { name: durante })).toBeDisabled()
  })
})
