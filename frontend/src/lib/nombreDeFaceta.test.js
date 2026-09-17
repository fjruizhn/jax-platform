import { describe, it, expect } from 'vitest'
import { nombreDeFaceta } from './nombreDeFaceta'

describe('nombreDeFaceta', () => {
  it('display_name, luego name, luego el id', () => {
    expect(nombreDeFaceta({ kimi: { display_name: 'Kimi K2', name: 'kimi' } }, 'kimi')).toBe('Kimi K2')
    expect(nombreDeFaceta({ kimi: { name: 'Kimi' } }, 'kimi')).toBe('Kimi')
    expect(nombreDeFaceta({}, 'kimi')).toBe('kimi')
    expect(nombreDeFaceta(null, 'kimi')).toBe('kimi')
  })
})
