import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

vi.mock('../../api/client', () => ({
  default: { get: vi.fn(() => Promise.resolve({ data: {} })), post: vi.fn() },
}))

import BottomBar from './BottomBar'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'

function renderBar() {
  return render(
    <I18nProvider>
      <BottomBar />
    </I18nProvider>
  )
}

// Ruling 30 (decisión de Fernando, "superficie + borde"): ningún texto va
// sobre un fondo sólido del color de la faceta. En modo chat, Enviar lleva
// borde y texto de la faceta activa sobre superficie.
describe('BottomBar -- botón Enviar en modo chat', () => {
  beforeEach(() => {
    useJaxStore.setState({ activeFacet: 'hyde' })
  })

  it('usa el token de la faceta activa en borde y texto, sin fondo de faceta', () => {
    renderBar()
    const enviar = screen.getByRole('button', { name: 'Enviar' })
    const estilo = enviar.getAttribute('style') || ''
    expect(estilo).toContain('border-color: rgb(var(--faceta-hyde) / 1)')
    expect(estilo).toMatch(/(^|;\s*)color: rgb\(var\(--faceta-hyde\) \/ 1\)/)
    expect(estilo).not.toContain('background')
    expect(enviar.className).toContain('bg-superficie')
    expect(enviar.className).not.toContain('text-fondo')
  })

  it('sigue a la faceta elegida', () => {
    renderBar()
    fireEvent.click(screen.getByRole('button', { name: /jekyll/i }))
    const estilo = screen.getByRole('button', { name: 'Enviar' }).getAttribute('style') || ''
    expect(estilo).toContain('rgb(var(--faceta-jekyll) / 1)')
    expect(estilo).not.toContain('background')
  })

  it('la faceta elegida pinta borde y texto con su token, sin fondo de color', () => {
    renderBar()
    const boton = screen.getByRole('button', { name: /hyde/i })
    const estilo = boton.getAttribute('style') || ''
    expect(estilo).toContain('border-color: rgb(var(--faceta-hyde) / 1)')
    expect(estilo).not.toContain('background')
    expect(boton.className).toContain('bg-superficie')
  })
})
