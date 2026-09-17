import { describe, it, expect } from 'vitest'
import { formatearUsd, OPCIONES_USD } from './moneda'

// Montos del pre-vuelo (spec 2026-09-17 §6.2): con el locale activo, nunca un
// "$" pegado a mano. Llegan como string decimal desde el backend.
describe('formatearUsd', () => {
  it('formatea en inglés con símbolo y dos decimales', () => {
    expect(formatearUsd('0.5', 'en')).toBe('$0.50')
    expect(formatearUsd('1234.5', 'en')).toBe('$1,234.50')
  })

  it('conserva hasta cuatro decimales para montos chicos', () => {
    expect(formatearUsd('0.123456', 'en')).toBe('$0.1235')
  })

  it('usa el locale del idioma activo', () => {
    expect(formatearUsd('1234.5', 'es')).toBe(new Intl.NumberFormat('es-HN', OPCIONES_USD).format(1234.5))
  })

  it('un monto ausente o ilegible no se inventa', () => {
    for (const malo of [null, undefined, '', 'abc']) expect(formatearUsd(malo, 'es')).toBeNull()
  })

  // Adenda Task 8 ítem 1: string de punto fijo o número JSON.
  it('acepta número y string de punto fijo con muchos decimales', () => {
    expect(formatearUsd(0.6, 'en')).toBe('$0.60')
    expect(formatearUsd('0.60', 'en')).toBe('$0.60')
    expect(formatearUsd('0.0000001', 'en')).toBe('$0.00')
  })
})
