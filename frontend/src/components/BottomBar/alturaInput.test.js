import { describe, it, expect } from 'vitest'

// La caja de mensaje crece con el texto (2026-09-12, pedido de Fernando): los
// prompts largos quedaban en UNA línea con scroll adentro y no se podían leer.
// Crece hasta MAX_LINEAS_INPUT (al menos 5 pedidas) y recién ahí hace scroll.
// jsdom no calcula layout, así que la cuenta vive en una función pura.
import { alturaInput, MAX_LINEAS_INPUT } from './alturaInput'

// Medidas reales del textarea: text-sm (line-height 20px), py-2 (8+8), borde 1+1.
const MEDIDAS = { lineHeight: 20, paddingY: 16, bordeY: 2 }
const altoDe = (lineas) => lineas * MEDIDAS.lineHeight + MEDIDAS.paddingY

describe('alturaInput', () => {
  it('permite al menos 5 líneas', () => {
    expect(MAX_LINEAS_INPUT).toBeGreaterThanOrEqual(5)
  })

  it('una línea: el alto de una línea, sin scroll', () => {
    const r = alturaInput({ scrollHeight: altoDe(1), ...MEDIDAS })
    expect(r).toEqual({ altoPx: altoDe(1) + MEDIDAS.bordeY, conScroll: false })
  })

  it('5 líneas: crece entero, sin recortar y sin scroll', () => {
    const r = alturaInput({ scrollHeight: altoDe(5), ...MEDIDAS })
    expect(r).toEqual({ altoPx: altoDe(5) + MEDIDAS.bordeY, conScroll: false })
  })

  it('un prompt de 20 líneas se topa en el máximo y recién ahí hace scroll', () => {
    const r = alturaInput({ scrollHeight: altoDe(20), ...MEDIDAS })
    expect(r).toEqual({ altoPx: altoDe(MAX_LINEAS_INPUT) + MEDIDAS.bordeY, conScroll: true })
  })
})
