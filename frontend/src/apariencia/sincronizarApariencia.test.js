import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../api/client', () => ({ default: { get: vi.fn() } }))

import api from '../api/client'
import { sincronizarApariencia } from './sincronizarApariencia'
import { useTema } from '../store/useTema'
import { useApariencia } from '../store/useApariencia'
import { aplicarTema } from '../tema/aplicarTema'

// Una sola petición a GET /apariencia por carga (frente C, 2026-09-16): trae el
// tema (spec 2026-09-14-tema-tokens §5), el idioma y el nombre del sistema.
const html = () => document.documentElement
const APARIENCIA = { lang_default: 'es', system_name: 'Axioma' }

beforeEach(() => {
  api.get.mockReset()
  localStorage.clear()
  aplicarTema('dark')
  useTema.setState({ theme: 'dark', predeterminado: null })
  useApariencia.setState({ systemName: null, langDefault: null })
  document.title = 'Axioma'
})

describe('sincronizarApariencia', () => {
  it('sin elección, el predeterminado del servidor se guarda y se aplica', async () => {
    api.get.mockResolvedValue({ data: { ...APARIENCIA, theme_default: 'light' } })
    await sincronizarApariencia()
    expect(api.get).toHaveBeenCalledWith('/apariencia')
    expect(localStorage.getItem('jax_theme_default')).toBe('light')
    expect(useTema.getState().theme).toBe('light')
    expect(html().getAttribute('data-tema')).toBe('claro')
  })

  it('con elección guardada, un predeterminado distinto no cambia el tema', async () => {
    localStorage.setItem('jax_theme', 'dark')
    api.get.mockResolvedValue({ data: { ...APARIENCIA, theme_default: 'light' } })
    await sincronizarApariencia()
    expect(localStorage.getItem('jax_theme_default')).toBe('light')
    expect(useTema.getState().theme).toBe('dark')
    expect(html().hasAttribute('data-tema')).toBe(false)
  })

  it('un valor fuera de lista del servidor se ignora', async () => {
    api.get.mockResolvedValue({ data: { ...APARIENCIA, theme_default: '<b>claro</b>' } })
    await sincronizarApariencia()
    expect(localStorage.getItem('jax_theme_default')).toBeNull()
    expect(useTema.getState().theme).toBe('dark')
  })

  it('si /apariencia falla, rechaza y se queda el último predeterminado conocido', async () => {
    localStorage.setItem('jax_theme_default', 'light')
    api.get.mockRejectedValue(new Error('red caída'))
    await expect(sincronizarApariencia()).rejects.toThrow('red caída')
    expect(localStorage.getItem('jax_theme_default')).toBe('light')
  })

  it('una sola petición deja el nombre, el idioma y el título del documento', async () => {
    api.get.mockResolvedValue({ data: { theme_default: 'dark', lang_default: 'en', system_name: 'Axioma Lab' } })
    await sincronizarApariencia()
    expect(api.get).toHaveBeenCalledTimes(1)
    expect(useApariencia.getState()).toMatchObject({ systemName: 'Axioma Lab', langDefault: 'en' })
    expect(document.title).toBe('Axioma Lab')
  })
})
