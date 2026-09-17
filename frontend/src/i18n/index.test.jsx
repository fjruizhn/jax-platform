import { render, screen, act } from '@testing-library/react'
import { describe, it, expect, beforeEach } from 'vitest'
import '@testing-library/jest-dom'
import { I18nProvider, useI18n, diccionarioActivo } from './index.jsx'
import es from './es.js'
import en from './en.js'
import { useApariencia } from '../store/useApariencia'

// lang_default (frente C, 2026-09-16): el idioma del sistema manda mientras
// la persona no elija; su elección (jax_lang) gana siempre.
function Idioma() {
  const { lang, t } = useI18n()
  return <span data-testid="idioma">{`${lang}|${t.emailLabel}`}</span>
}

beforeEach(() => {
  localStorage.clear()
  useApariencia.setState({ systemName: null, langDefault: null })
})

describe('I18nProvider con idioma predeterminado', () => {
  it('sin elección, el predeterminado del sistema cambia la interfaz sin guardarse como elección', async () => {
    render(<I18nProvider><Idioma /></I18nProvider>)
    expect(screen.getByTestId('idioma').textContent.startsWith('es|')).toBe(true)
    act(() => useApariencia.getState().fijar({ lang_default: 'en' }))
    expect(screen.getByTestId('idioma').textContent.startsWith('en|')).toBe(true)
    expect(localStorage.getItem('jax_lang')).toBeNull()
  })

  it('con una elección guardada, el predeterminado no la pisa', () => {
    localStorage.setItem('jax_lang', 'es')
    render(<I18nProvider><Idioma /></I18nProvider>)
    act(() => useApariencia.getState().fijar({ lang_default: 'en' }))
    expect(screen.getByTestId('idioma').textContent.startsWith('es|')).toBe(true)
  })
})

// Rebase sobre frente A (2026-09-17): el store traduce con diccionarioActivo
// (A-29); tiene que seguir la MISMA regla que el proveedor, no solo jax_lang.
describe('diccionarioActivo (store fuera de React)', () => {
  it('sin elección, sigue el último lang_default conocido del sistema', () => {
    localStorage.setItem('jax_lang_default', 'en')
    expect(diccionarioActivo()).toBe(en)
  })

  it('la elección de la persona gana sobre lang_default', () => {
    localStorage.setItem('jax_lang_default', 'en')
    localStorage.setItem('jax_lang', 'es')
    expect(diccionarioActivo()).toBe(es)
  })
})
