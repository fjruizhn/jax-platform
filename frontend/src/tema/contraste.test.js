// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { parsearTokens, contraste } from './contraste.js'
import { TOKENS, PARES, AA_TEXTO, colorToken } from './tokens.js'

// Test de contraste del tema (spec 2026-09-14-tema-tokens-design.md §6).
// Reemplaza a lightModeOverrides.test.js, que exigía que un override
// EXISTIERA, no que se leyera. Lee tokens.css por fs, en entorno node: vitest
// excluye el CSS del pipeline y un `?raw` llega vacío (medido en
// lightModeOverrides.test.js).
const css = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8')
const temas = parsearTokens(css)

function fallas(tema) {
  return PARES.flatMap(([frente, fondo, minimo]) => {
    const r = contraste(temas[tema][frente], temas[tema][fondo])
    return r >= minimo ? [] : [`${frente} sobre ${fondo} en ${tema}: ${r.toFixed(2)} < ${minimo}`]
  })
}

describe('tokens de color', () => {
  it('tokens.css tiene los dos temas con 47 tokens cada uno', () => {
    expect(Object.keys(temas.oscuro)).toHaveLength(47)
    expect(Object.keys(temas.claro)).toHaveLength(47)
  })

  it('todo token de tokens.js tiene valor en los dos temas, y todo valor tiene nombre', () => {
    const faltan = TOKENS.flatMap((n) => ['oscuro', 'claro'].filter((t) => !temas[t][n]).map((t) => `${n} (${t})`))
    const nombres = new Set([...Object.keys(temas.oscuro), ...Object.keys(temas.claro)])
    const sinNombre = [...nombres].filter((n) => !TOKENS.includes(n))
    expect(faltan).toEqual([])
    expect(sinNombre).toEqual([])
  })

  it('control negativo: el gris viejo (slate-500) sobre el fondo oscuro falla AA', () => {
    // Si la función de contraste no falla con esto, el resto del test no valida nada.
    const r = contraste([100, 116, 139], temas.oscuro.fondo)
    expect(r).toBeCloseTo(3.75, 1)
    expect(r).toBeLessThan(AA_TEXTO)
  })

  it('los 88 pares alcanzan su mínimo en oscuro', () => {
    expect(PARES).toHaveLength(88)
    expect(fallas('oscuro')).toEqual([])
  })

  it('los 88 pares alcanzan su mínimo en claro', () => {
    expect(fallas('claro')).toEqual([])
  })

  it('colorToken arma rgb(var()) y un nombre desconocido cae en texto-suave', () => {
    expect(colorToken('faceta-hyde')).toBe('rgb(var(--faceta-hyde) / 1)')
    expect(colorToken('peligro', 0.12)).toBe('rgb(var(--peligro) / 0.12)')
    expect(colorToken('no-existe')).toBe('rgb(var(--texto-suave) / 1)')
  })
})
