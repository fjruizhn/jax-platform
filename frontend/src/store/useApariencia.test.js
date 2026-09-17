import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useApariencia } from './useApariencia'

// Nombre del sistema e idioma predeterminado (frente C, 2026-09-16): llegan por
// GET /apariencia. El último conocido se guarda en este navegador (conveniencia
// por visitante) para titular la pestaña antes de la próxima respuesta.
beforeEach(() => {
  localStorage.clear()
  useApariencia.setState({ systemName: null, langDefault: null })
  document.title = 'Axioma'
})

describe('useApariencia', () => {
  it('fijar guarda el nombre y el idioma y titula el documento', () => {
    useApariencia.getState().fijar({ system_name: 'Hal', lang_default: 'en' })
    expect(useApariencia.getState()).toMatchObject({ systemName: 'Hal', langDefault: 'en' })
    expect(localStorage.getItem('jax_system_name')).toBe('Hal')
    expect(localStorage.getItem('jax_lang_default')).toBe('en')
    expect(document.title).toBe('Hal')
  })

  it('un nombre vacío, de espacios o que no es texto no se aplica; un idioma fuera de lista tampoco', () => {
    useApariencia.setState({ systemName: 'Axioma' })
    for (const valor of ['', '   ', 42, null, undefined]) {
      useApariencia.getState().fijar({ system_name: valor, lang_default: 'fr' })
      expect(useApariencia.getState()).toMatchObject({ systemName: 'Axioma', langDefault: null })
    }
    expect(document.title).toBe('Axioma')
  })

  it('al cargar el módulo toma el último nombre conocido y titula', async () => {
    localStorage.setItem('jax_system_name', 'Hal')
    localStorage.setItem('jax_lang_default', 'en')
    vi.resetModules()
    const { useApariencia: fresco } = await import('./useApariencia')
    expect(fresco.getState()).toMatchObject({ systemName: 'Hal', langDefault: 'en' })
    expect(document.title).toBe('Hal')
  })

  it('con localStorage bloqueado, importar y fijar no lanzan', async () => {
    const getItemOriginal = Storage.prototype.getItem
    const setItemOriginal = Storage.prototype.setItem
    Storage.prototype.getItem = () => { throw new DOMException('bloqueado', 'SecurityError') }
    Storage.prototype.setItem = () => { throw new DOMException('bloqueado', 'SecurityError') }
    try {
      vi.resetModules()
      const { useApariencia: fresco } = await import('./useApariencia')
      expect(fresco.getState().systemName).toBeNull()
      expect(() => fresco.getState().fijar({ system_name: 'Hal', lang_default: 'es' })).not.toThrow()
      expect(fresco.getState().systemName).toBe('Hal')
    } finally {
      Storage.prototype.getItem = getItemOriginal
      Storage.prototype.setItem = setItemOriginal
    }
  })
})
