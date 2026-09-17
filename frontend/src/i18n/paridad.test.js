import { describe, it, expect } from 'vitest'
import es from './es.js'
import en from './en.js'

// Paridad es/en (frente A, 2026-09-16): hasta hoy cada test miraba "su" clave;
// ninguno impedía que un idioma ganara o perdiera una. Incluye los objetos
// anidados (erroresMesa, avisosChat, smtpErrors...). Medido en 26c9cd5: 470/470.
function claves(obj, prefijo = '') {
  return Object.entries(obj).flatMap(([k, v]) =>
    v && typeof v === 'object' && !Array.isArray(v) ? claves(v, `${prefijo}${k}.`) : [`${prefijo}${k}`])
}

describe('i18n', () => {
  it('es y en tienen exactamente las mismas claves', () => {
    expect(claves(en).sort()).toEqual(claves(es).sort())
  })

  it('cada clave es del mismo tipo en los dos idiomas', () => {
    const tipo = (d, ruta) => typeof ruta.split('.').reduce((o, k) => o[k], d)
    for (const ruta of claves(es)) expect([ruta, tipo(en, ruta)]).toEqual([ruta, tipo(es, ruta)])
  })
})
