// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { parsearTokens, contraste } from './contraste.js'
import { TOKENS, PARES, AA_TEXTO, colorToken, EXENTOS_TEXTO, PERMITIDOS_CRUDOS, MIGRADOS } from './tokens.js'

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

// Uso (spec §6.4): un archivo ya migrado no vuelve a pintar con colores crudos.
// "Ya migrado" es MIGRADOS en tokens.js; cada PR del rollout lo amplía.
const fuentes = import.meta.glob(['../**/*.{js,jsx}', '!../**/*.test.{js,jsx}'], {
  query: '?raw', import: 'default', eager: true,
})
const porRuta = Object.fromEntries(Object.entries(fuentes).map(([k, v]) => [k.replace(/^\.\.\//, ''), v]))

const PALETA = 'slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose'
const PROPIEDAD = 'bg|text|border|divide|ring|placeholder|fill|stroke|from|via|to|outline|accent|caret|decoration|shadow'
const PROHIBIDOS = [
  ['clase de paleta de Tailwind', new RegExp(`(?<![\\w-])(?:${PROPIEDAD})-(?:(?:${PALETA})-\\d{2,3}|white|black)(?:\\/\\d+)?(?![\\w-])`, 'g')],
  ['color hal-*', /(?<![\w-])(?:bg|text|border)-hal-[a-z]+/g],
  ['hex', /#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])/g],
  ['rgb()/rgba() literal', /rgba?\(\s*\d/g],
  ['prefijo dark:', /(?<![\w-])dark:[a-z]/g],
  ['color con nombre en SVG o estilo', /(?:fill|stroke)=["'](?:white|black)["']|:\s*['"](?:white|black)['"]/g],
]

describe('uso de tokens en los archivos migrados', () => {
  it('los archivos migrados pintan sólo con tokens', () => {
    expect(MIGRADOS.length).toBeGreaterThan(0) // verde sobre cero archivos no vale
    const hallazgos = []
    for (const ruta of MIGRADOS) {
      const codigo = porRuta[ruta]
      if (codigo === undefined) {
        hallazgos.push(`${ruta}: no existe (¿ruta mal escrita en MIGRADOS?)`)
        continue
      }
      for (const [que, patron] of PROHIBIDOS) {
        for (const m of codigo.match(patron) || []) {
          if (PERMITIDOS_CRUDOS.some((p) => p.archivo === ruta && p.texto === m)) continue
          hallazgos.push(`${ruta}: ${que} «${m}»`)
        }
      }
    }
    expect(hallazgos).toEqual([])
  })

  it('un token que no es primer plano de ningún par no se usa como text-* (salvo exentos)', () => {
    const frentes = new Set(PARES.map(([f]) => f))
    const hallazgos = []
    for (const ruta of MIGRADOS) {
      for (const [, token] of (porRuta[ruta] || '').matchAll(/(?<![\w-])text-([a-z0-9-]+)/g)) {
        if (!TOKENS.includes(token) || frentes.has(token)) continue
        if (EXENTOS_TEXTO[token]?.archivos.includes(ruta)) continue
        hallazgos.push(`${ruta}: text-${token}`)
      }
    }
    expect(hallazgos).toEqual([])
  })
})
