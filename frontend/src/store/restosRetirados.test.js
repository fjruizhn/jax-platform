import { beforeEach, describe, expect, it } from 'vitest'
import { limpiarRestosRetirados } from './restosRetirados'

// T16 (2026-10-02): el modo Comando se retiró y con él la lista de tareas pendientes que
// guardaba en localStorage. Los navegadores que ya la tenían la limpian una vez al arrancar.
describe('limpiarRestosRetirados', () => {
  beforeEach(() => localStorage.clear())

  it('borra jax_pending_cmds y no toca ninguna otra clave', () => {
    localStorage.setItem('jax_pending_cmds', JSON.stringify({ owner: 1, ids: ['a'] }))
    localStorage.setItem('jax_lang', 'es')
    limpiarRestosRetirados()
    expect(localStorage.getItem('jax_pending_cmds')).toBeNull()
    expect(localStorage.getItem('jax_lang')).toBe('es')
  })

  it('sin la clave no hace nada y puede correr dos veces', () => {
    limpiarRestosRetirados()
    limpiarRestosRetirados()
    expect(localStorage.length).toBe(0)
  })

  it('un localStorage que lanza no tumba el arranque', () => {
    const original = Storage.prototype.removeItem
    Storage.prototype.removeItem = () => { throw new Error('cuota o modo privado') }
    try {
      expect(() => limpiarRestosRetirados()).not.toThrow()
    } finally {
      Storage.prototype.removeItem = original
    }
  })
})
