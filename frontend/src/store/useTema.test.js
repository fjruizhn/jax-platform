import { describe, it, expect, vi, beforeEach } from 'vitest'

import { useTema } from './useTema'
import { aplicarTema } from '../tema/aplicarTema'

// Tema (spec 2026-09-14-tema-tokens §5): si el usuario eligió, gana su
// elección; si no, el theme_default del sistema, que llega por GET /apariencia
// (apariencia/sincronizarApariencia.js).
const html = () => document.documentElement

beforeEach(() => {
  localStorage.clear()
  aplicarTema('dark')
  useTema.setState({ theme: 'dark', predeterminado: null })
})

describe('useTema', () => {
  it('toggleTheme escribe la elección y aplica los dos temas', () => {
    useTema.getState().toggleTheme()
    expect(localStorage.getItem('jax_theme')).toBe('light')
    expect(html().getAttribute('data-tema')).toBe('claro')
    useTema.getState().toggleTheme()
    expect(localStorage.getItem('jax_theme')).toBe('dark')
    expect(html().hasAttribute('data-tema')).toBe(false)
  })

  // M-4 (revisión final, 2026-09-14): con el almacenamiento bloqueado (Safari
  // con cookies bloqueadas, iframe con sandbox), localStorage.getItem/setItem
  // lanzan SecurityError. temaInicial() y predeterminadoGuardado() corren al
  // EVALUAR el módulo (dentro de create(...)) -- sin try/catch, importar
  // useTema.js tira y la pantalla de Login queda en blanco. Se reimporta el
  // módulo fresco (vi.resetModules) con Storage.prototype parcheada para
  // lanzar, para que temaInicial() corra bajo el bloqueo real.
  // Decisión de Fernando (2026-09-14, brief fix-vivo-brief.md §B): guardar el
  // predeterminado en Configuración también fija la elección del propio
  // admin, aunque tuviera otra. sincronizarApariencia (al cargar la app)
  // NO cambia: sigue sin pisar la elección de nadie.
  it('fijarPredeterminadoComoEleccion pisa una elección existente', () => {
    localStorage.setItem('jax_theme', 'dark')
    useTema.getState().fijarPredeterminadoComoEleccion('light')
    expect(useTema.getState().theme).toBe('light')
    expect(localStorage.getItem('jax_theme')).toBe('light')
    expect(localStorage.getItem('jax_theme_default')).toBe('light')
    expect(useTema.getState().predeterminado).toBe('light')
    expect(html().getAttribute('data-tema')).toBe('claro')
  })

  it('fijarPredeterminadoComoEleccion ignora un valor fuera de lista', () => {
    localStorage.setItem('jax_theme', 'dark')
    useTema.getState().fijarPredeterminadoComoEleccion('<b>claro</b>')
    expect(useTema.getState().theme).toBe('dark')
    expect(localStorage.getItem('jax_theme')).toBe('dark')
    expect(localStorage.getItem('jax_theme_default')).toBeNull()
  })

  it('con localStorage bloqueado, el store arranca en dark y toggleTheme no lanza', async () => {
    const getItemOriginal = Storage.prototype.getItem
    const setItemOriginal = Storage.prototype.setItem
    Storage.prototype.getItem = () => { throw new DOMException('bloqueado', 'SecurityError') }
    Storage.prototype.setItem = () => { throw new DOMException('bloqueado', 'SecurityError') }
    try {
      vi.resetModules()
      const { useTema: useTemaFresco } = await import('./useTema')
      expect(useTemaFresco.getState().theme).toBe('dark')
      expect(useTemaFresco.getState().predeterminado).toBeNull()
      expect(() => useTemaFresco.getState().toggleTheme()).not.toThrow()
    } finally {
      Storage.prototype.getItem = getItemOriginal
      Storage.prototype.setItem = setItemOriginal
    }
  })
})
