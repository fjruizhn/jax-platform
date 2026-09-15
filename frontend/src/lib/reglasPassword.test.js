import { describe, it, expect } from 'vitest'
import { problemaDePassword } from './reglasPassword'

// La misma regla que backend/auth/password_rules.py (2026-09-12, etapa 4).
describe('problemaDePassword', () => {
  it('menos de 8 caracteres es corta', () => {
    expect(problemaDePassword('1234567')).toBe('corta')
    expect(problemaDePassword('12345678')).toBe(null)
  })

  it('más de 72 BYTES es larga aunque tenga menos de 72 caracteres', () => {
    expect(problemaDePassword('ñ'.repeat(37))).toBe('larga')
    expect(problemaDePassword('ñ'.repeat(36))).toBe(null)
  })

  it('cuenta caracteres como el backend: un emoji es uno (no dos unidades UTF-16)', () => {
    expect(problemaDePassword('😀'.repeat(7))).toBe('corta')
  })
})
