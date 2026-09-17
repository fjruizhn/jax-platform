import { describe, it, expect } from 'vitest'
import { idiomaInicial } from './idioma'

const almacen = (datos) => ({ getItem: (k) => (k in datos ? datos[k] : null) })

describe('idiomaInicial', () => {
  it('la elección del usuario gana', () => {
    expect(idiomaInicial(almacen({ jax_lang: 'es', jax_lang_default: 'en' }))).toBe('es')
  })
  it('sin elección, el predeterminado del sistema', () => {
    expect(idiomaInicial(almacen({ jax_lang_default: 'en' }))).toBe('en')
  })
  it('sin nada, español', () => {
    expect(idiomaInicial(almacen({}))).toBe('es')
  })
  it('valores fuera de lista se ignoran', () => {
    expect(idiomaInicial(almacen({ jax_lang: 'fr', jax_lang_default: '<b>' }))).toBe('es')
    expect(idiomaInicial(almacen({ jax_lang: 'fr', jax_lang_default: 'en' }))).toBe('en')
  })
  it('con el almacenamiento bloqueado, español sin lanzar', () => {
    const bloqueado = { getItem: () => { throw new DOMException('bloqueado', 'SecurityError') } }
    expect(idiomaInicial(bloqueado)).toBe('es')
  })
})
