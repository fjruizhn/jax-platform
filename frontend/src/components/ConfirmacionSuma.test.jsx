import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import '@testing-library/jest-dom'
import ConfirmacionSuma, { numerosAlAzar } from './ConfirmacionSuma'
import { I18nProvider } from '../i18n/index.jsx'

// Confirmación por suma (2026-09-12, admin usuarios etapa 5): el botón
// destructivo se habilita solo con la respuesta correcta. Reusable para todo
// borrado futuro; reemplaza a window.confirm. Va sobre Dialogo (Ruling U28).
function renderSuma(props = {}) {
  const onConfirmar = props.onConfirmar || vi.fn().mockResolvedValue()
  const onCancelar = props.onCancelar || vi.fn()
  const mensaje = props.mensaje || 'Irreversible.'
  render(
    <I18nProvider>
      <ConfirmacionSuma titulo="Dar de baja a b@x.io" mensaje={mensaje} textoConfirmar="Dar de baja"
        onConfirmar={onConfirmar} onCancelar={onCancelar} numeros={[12, 7]} />
    </I18nProvider>
  )
  return { onConfirmar, onCancelar }
}

describe('ConfirmacionSuma', () => {
  it('el botón destructivo se habilita solo con la respuesta correcta', () => {
    renderSuma()
    const boton = screen.getByRole('button', { name: 'Dar de baja' })
    expect(boton).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Resolvé 12 + 7 = ?'), { target: { value: '18' } })
    expect(boton).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Resolvé 12 + 7 = ?'), { target: { value: '19' } })
    expect(boton).toBeEnabled()
  })

  it('con la respuesta correcta, confirmar llama a onConfirmar', async () => {
    const { onConfirmar } = renderSuma()
    fireEvent.change(screen.getByLabelText('Resolvé 12 + 7 = ?'), { target: { value: '19' } })
    fireEvent.click(screen.getByRole('button', { name: 'Dar de baja' }))
    await waitFor(() => expect(onConfirmar).toHaveBeenCalledTimes(1))
  })

  it('cancelar llama a onCancelar y no confirma', () => {
    const { onConfirmar, onCancelar } = renderSuma()
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }))
    expect(onCancelar).toHaveBeenCalledTimes(1)
    expect(onConfirmar).not.toHaveBeenCalled()
  })

  it('los números al azar quedan en rango (a de 10 a 49, b de 1 a 9)', () => {
    expect(numerosAlAzar(() => 0)).toEqual([10, 1])
    expect(numerosAlAzar(() => 0.9999)).toEqual([49, 9])
  })

  // Dispatch de la Task 4 (Ruling U28): Dialogo pone Escape; Escape cancela.
  it('Escape llama a onCancelar y no confirma', () => {
    const { onConfirmar, onCancelar } = renderSuma()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onCancelar).toHaveBeenCalledTimes(1)
    expect(onConfirmar).not.toHaveBeenCalled()
  })
})

// M4 (revisión adversarial de jax-platform PR 146, tercera vuelta):
// `mensaje` puede traer una palabra larga y sin espacios (la pantalla de
// Memoria lo pasa por `superviviente_texto`, que no está garantizado que
// tenga puntuación) -- el párrafo tiene que partirla (`break-words`), no
// desbordar el panel de ancho acotado (`Dialogo`, `max-w-md` por defecto).
describe('ConfirmacionSuma -- mensaje largo sin espacios', () => {
  it('el párrafo del mensaje lleva break-words', () => {
    const largoSinEspacios = 'a'.repeat(400)
    renderSuma({ mensaje: largoSinEspacios })
    const parrafo = screen.getByText(largoSinEspacios)
    expect(parrafo.className).toContain('break-words')
  })

  it('un mensaje largo sin espacios no rompe el render ni los otros usos', () => {
    // No rompe: sigue montando el diálogo entero, con su formulario y sus
    // botones, igual que con un mensaje corto (los otros usos de
    // ConfirmacionSuma -- AdminUsers, AdminRepository, KillSwitch -- pasan
    // mensajes cortos, y esto confirma que el cambio no les afecta).
    const largoSinEspacios = 'palabralarguisima'.repeat(30)
    renderSuma({ mensaje: largoSinEspacios })
    expect(screen.getByText(largoSinEspacios)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancelar' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Dar de baja' })).toBeDisabled()
  })
})

// Fix round 1 de la Task 4: con la primera confirmación en vuelo, ni un
// segundo clic ni un segundo submit (Enter) vuelven a confirmar.
describe('ConfirmacionSuma -- doble envío', () => {
  it('mientras confirma, otro clic u otro submit no llaman de nuevo a onConfirmar', async () => {
    let resolver
    const onConfirmar = vi.fn(() => new Promise((r) => { resolver = r }))
    renderSuma({ onConfirmar })
    fireEvent.change(screen.getByLabelText('Resolvé 12 + 7 = ?'), { target: { value: '19' } })
    const boton = screen.getByRole('button', { name: 'Dar de baja' })
    fireEvent.click(boton)
    fireEvent.click(boton)
    fireEvent.submit(boton.closest('form'))
    fireEvent.submit(boton.closest('form'))
    expect(onConfirmar).toHaveBeenCalledTimes(1)
    resolver()
    await waitFor(() => expect(boton).toBeEnabled())
  })
})
