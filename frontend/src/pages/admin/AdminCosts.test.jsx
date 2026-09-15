import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Re-revisión acotada (2026-09-15, Fernando: sin diferidos): AdminCosts.jsx
// formateaba tokens_in/tokens_out con toLocaleString() sin locale -- seguía
// el locale del NAVEGADOR, no el idioma activo de la app (mismo defecto que
// I-1 arreglaba en las fechas de AdminRepository/AdminUsers, pero acá con
// números). Se usa el mismo localeFor(lang) del contexto i18n.
//
// Nota de verificación (Principio I, "el que supone se equivoca"): medido
// con este Node/ICU, es-HN y en-US dan el MISMO agrupamiento de miles para
// enteros (Honduras usa la misma convención que EE.UU.: coma de millar,
// punto decimal -- distinto de es-ES/es genérico, que usan punto). Por eso
// esta prueba no puede comparar el TEXTO renderizado entre idiomas (sería
// igual en los dos); en su lugar espía Number.prototype.toLocaleString y
// verifica el argumento de locale que realmente recibe, que es lo que
// localeFor(lang) cambia.
vi.mock('../../api/client', () => ({ default: { get: vi.fn() } }))

import api from '../../api/client'
import AdminCosts from './AdminCosts'
import { I18nProvider } from '../../i18n/index.jsx'

const FILA = {
  facet: 'hyde', model: 'gpt-x', tokens_in: 1234567, tokens_out: 890123,
  cost_usd: 1.234567, unpriced_requests: 0, requests: 42,
}
const DATA = { by_facet: [FILA], chart_data: null }

function renderCosts() {
  return render(<I18nProvider><AdminCosts /></I18nProvider>)
}

beforeEach(() => {
  api.get.mockReset()
  api.get.mockResolvedValue({ data: DATA })
  localStorage.clear()
})

describe('AdminCosts -- locale de números (re-revisión, sin diferidos)', () => {
  it('en inglés, tokens_in/tokens_out se formatean con en-US, no con el locale del navegador', async () => {
    const spy = vi.spyOn(Number.prototype, 'toLocaleString')
    localStorage.setItem('jax_lang', 'en')
    renderCosts()
    await screen.findByText('gpt-x')
    expect(spy).toHaveBeenCalledWith('en-US')
    spy.mockRestore()
  })

  it('en español (por defecto), tokens_in/tokens_out se formatean con es-HN', async () => {
    const spy = vi.spyOn(Number.prototype, 'toLocaleString')
    renderCosts()
    await screen.findByText('gpt-x')
    expect(spy).toHaveBeenCalledWith('es-HN')
    spy.mockRestore()
  })

  it('el valor formateado sale correcto (1.234.567 con agrupamiento, cualquiera sea el locale)', async () => {
    renderCosts()
    expect(await screen.findByText('1,234,567')).toBeInTheDocument()
    expect(await screen.findByText('890,123')).toBeInTheDocument()
  })
})
