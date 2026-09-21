import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, beforeEach } from 'vitest'
import '@testing-library/jest-dom'
import Toast from './Toast'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'
import { useJaxStore } from '../../store/useJaxStore'

// `t.toastCerrar` sale de i18n (no un string fijo en el componente): las dos
// traducciones tienen que existir y no coincidir por casualidad con el
// glifo del botón.

// Hallazgo de revisión 2026-09-21: el botón de cerrar no tenía aria-label
// ni title -- su nombre accesible era el glifo "×", que un lector de
// pantalla anuncia como "multiplicación". WCAG 4.1.2 (Name, Role, Value).

function renderToast() {
  return render(<I18nProvider><Toast /></I18nProvider>)
}

beforeEach(() => {
  useJaxStore.setState({ toasts: [] })
})

describe('Toast', () => {
  it('no pinta nada sin avisos', () => {
    const { container } = renderToast()
    expect(container).toBeEmptyDOMElement()
  })

  it('el botón de cerrar tiene nombre accesible desde i18n, no el glifo crudo', () => {
    useJaxStore.setState({ toasts: [{ id: 1, type: 'info', message: 'x' }] })
    renderToast()
    const boton = screen.getByRole('button', { name: es.toastCerrar })
    expect(boton).toBeInTheDocument()
    // El nombre accesible NO puede ser el glifo "×" -- un lector de
    // pantalla lo anunciaría como "multiplicación", no "cerrar".
    expect(boton).not.toHaveAccessibleName('×')
  })

  it('la traducción existe en los dos idiomas y no está vacía (i18n real, no un string fijo)', () => {
    expect(es.toastCerrar).toBeTypeOf('string')
    expect(es.toastCerrar.length).toBeGreaterThan(0)
    expect(en.toastCerrar).toBeTypeOf('string')
    expect(en.toastCerrar.length).toBeGreaterThan(0)
    expect(en.toastCerrar).not.toBe(es.toastCerrar)
  })

  it('clic en cerrar saca el aviso', () => {
    useJaxStore.setState({ toasts: [{ id: 1, type: 'info', message: 'x' }] })
    renderToast()
    fireEvent.click(screen.getByRole('button', { name: es.toastCerrar }))
    expect(useJaxStore.getState().toasts).toEqual([])
  })
})
