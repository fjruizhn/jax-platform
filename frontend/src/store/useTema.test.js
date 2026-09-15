import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../api/client', () => ({ default: { get: vi.fn() } }))

import api from '../api/client'
import { useTema } from './useTema'
import { aplicarTema } from '../tema/aplicarTema'

// Tema (spec 2026-09-14-tema-tokens §5): si el usuario eligió, gana su
// elección; si no, el theme_default del sistema, que llega por GET /apariencia.
const html = () => document.documentElement

beforeEach(() => {
  api.get.mockReset()
  localStorage.clear()
  aplicarTema('dark')
  useTema.setState({ theme: 'dark', predeterminado: null })
})

describe('useTema', () => {
  it('sin elección, el predeterminado del servidor se guarda y se aplica', async () => {
    api.get.mockResolvedValue({ data: { theme_default: 'light' } })
    await useTema.getState().sincronizarPredeterminado()
    expect(api.get).toHaveBeenCalledWith('/apariencia')
    expect(localStorage.getItem('jax_theme_default')).toBe('light')
    expect(useTema.getState().theme).toBe('light')
    expect(html().getAttribute('data-tema')).toBe('claro')
    expect(html().classList.contains('light-mode')).toBe(true)
  })

  it('con elección guardada, un predeterminado distinto no cambia el tema', async () => {
    localStorage.setItem('jax_theme', 'dark')
    api.get.mockResolvedValue({ data: { theme_default: 'light' } })
    await useTema.getState().sincronizarPredeterminado()
    expect(localStorage.getItem('jax_theme_default')).toBe('light')
    expect(useTema.getState().theme).toBe('dark')
    expect(html().hasAttribute('data-tema')).toBe(false)
  })

  it('toggleTheme escribe la elección y aplica los dos temas', () => {
    useTema.getState().toggleTheme()
    expect(localStorage.getItem('jax_theme')).toBe('light')
    expect(html().getAttribute('data-tema')).toBe('claro')
    useTema.getState().toggleTheme()
    expect(localStorage.getItem('jax_theme')).toBe('dark')
    expect(html().hasAttribute('data-tema')).toBe(false)
    expect(html().classList.contains('light-mode')).toBe(false)
  })

  it('un valor fuera de lista del servidor se ignora', async () => {
    api.get.mockResolvedValue({ data: { theme_default: '<b>claro</b>' } })
    await useTema.getState().sincronizarPredeterminado()
    expect(localStorage.getItem('jax_theme_default')).toBeNull()
    expect(useTema.getState().theme).toBe('dark')
  })

  it('si /apariencia falla, rechaza y se queda el último predeterminado conocido', async () => {
    localStorage.setItem('jax_theme_default', 'light')
    api.get.mockRejectedValue(new Error('red caída'))
    await expect(useTema.getState().sincronizarPredeterminado()).rejects.toThrow('red caída')
    expect(localStorage.getItem('jax_theme_default')).toBe('light')
  })
})
