import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Caja de contraseña con ojito (2026-09-12, pedido de Fernando): toda caja de
// contraseña de la UI deja ver lo que se escribió. Antes solo Login lo tenía;
// ResetPassword usaba un emoji sin etiqueta y controlaba sus DOS cajas con un
// solo botón, y las de admin (alta de usuario, API key) no tenían nada.
import PasswordInput from './PasswordInput'
import { I18nProvider } from '../i18n/index.jsx'

function renderCon(ui) {
  return render(<I18nProvider>{ui}</I18nProvider>)
}

beforeEach(() => localStorage.clear())

describe('PasswordInput', () => {
  it('arranca oculta y el ojito la muestra y la vuelve a ocultar', () => {
    renderCon(<PasswordInput placeholder="clave" defaultValue="secreta" />)
    const caja = screen.getByPlaceholderText('clave')
    expect(caja).toHaveAttribute('type', 'password')

    fireEvent.click(screen.getByRole('button', { name: 'Mostrar contraseña' }))
    expect(caja).toHaveAttribute('type', 'text')

    fireEvent.click(screen.getByRole('button', { name: 'Ocultar contraseña' }))
    expect(caja).toHaveAttribute('type', 'password')
  })

  it('el ojito no envía el formulario', () => {
    const enviar = vi.fn((e) => e.preventDefault())
    renderCon(<form onSubmit={enviar}><PasswordInput placeholder="clave" /></form>)
    fireEvent.click(screen.getByRole('button', { name: 'Mostrar contraseña' }))
    expect(enviar).not.toHaveBeenCalled()
  })

  it('cada caja tiene su propio ojito: ver una no destapa la otra', () => {
    renderCon(<><PasswordInput placeholder="nueva" /><PasswordInput placeholder="confirmar" /></>)
    fireEvent.click(screen.getAllByRole('button', { name: 'Mostrar contraseña' })[0])
    expect(screen.getByPlaceholderText('nueva')).toHaveAttribute('type', 'text')
    expect(screen.getByPlaceholderText('confirmar')).toHaveAttribute('type', 'password')
  })

  it('pasa value, onChange y el resto de las props al input', () => {
    const cambio = vi.fn()
    renderCon(<PasswordInput placeholder="clave" value="abc" onChange={cambio} required className="mi-clase" />)
    const caja = screen.getByPlaceholderText('clave')
    expect(caja).toHaveValue('abc')
    expect(caja).toBeRequired()
    expect(caja).toHaveClass('mi-clase')
    fireEvent.change(caja, { target: { value: 'abcd' } })
    expect(cambio).toHaveBeenCalled()
  })
})
