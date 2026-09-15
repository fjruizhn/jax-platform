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

  // Fix round 1 (2026-09-15): el caso de la ñ saltaba de 72 a 74 bytes y no
  // pisaba el borde -- un mutante que sube BCRYPT_MAX_BYTES a 73 pasaba sin
  // que ningún test lo notara. Estos dos casos SÍ caen justo en 72 y 73 bytes
  // (70/71 caracteres ASCII de 1 byte + una ñ de 2 bytes en UTF-8).
  it('exactamente 72 bytes (el borde) no es larga', () => {
    expect(problemaDePassword('a'.repeat(70) + 'ñ')).toBe(null)
  })

  it('73 bytes (un byte más que el borde) ya es larga', () => {
    expect(problemaDePassword('a'.repeat(71) + 'ñ')).toBe('larga')
  })
})
