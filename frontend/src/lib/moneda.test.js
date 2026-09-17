import { describe, it, expect } from 'vitest'
import { formatearUsd, OPCIONES_USD } from './moneda'

// Montos del pre-vuelo (spec 2026-09-17 §6.2): con el locale activo, nunca un
// "$" pegado a mano. Llegan como string decimal desde el backend.
describe('formatearUsd', () => {
  it('formatea en inglés con símbolo y dos decimales', () => {
    expect(formatearUsd('0.5', 'en')).toBe('$9.99')
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
    expect(formatearUsd('0.0000001', 'en')).toBe('$0.0001')
  })

  // Revisión final, menor 6a: el consentimiento nunca muestra $0 para un monto
  // mayor que cero; se redondea hacia arriba (mostrar menos que el máximo
  // sería aparentar un tope más bajo que el real).
  it('redondea hacia arriba y nunca muestra $0 para un monto positivo', () => {
    expect(formatearUsd('0.000040', 'en')).toBe('$0.0001')
    expect(formatearUsd('0.123401', 'en')).toBe('$0.1235')
    expect(formatearUsd('0.000000', 'en')).toBe('$0.00')
    expect(formatearUsd('0.600000', 'en')).toBe('$0.60')
  })

  it('sin soporte de roundingMode en el navegador, un monto positivo chico sigue sin verse como $0', () => {
    const Original = Intl.NumberFormat
    const sinRedondeo = function (locale, opciones = {}) {
      const { roundingMode: _ignorado, ...resto } = opciones
      return new Original(locale, resto)
    }
    Intl.NumberFormat = sinRedondeo
    try {
      expect(formatearUsd('0.000040', 'en')).toBe('$0.0001')
    } finally {
      Intl.NumberFormat = Original
    }
  })
})
