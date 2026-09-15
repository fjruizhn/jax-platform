// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { parsearTokens, contraste } from './contraste.js'
import { TOKENS, PARES, AA_TEXTO, colorToken, tokenDeFaceta, EXENTOS_TEXTO, PERMITIDOS_CRUDOS } from './tokens.js'

// Test de contraste del tema (spec 2026-09-14-tema-tokens-design.md §6).
// Reemplaza a lightModeOverrides.test.js, que exigía que un override
// EXISTIERA, no que se leyera. Lee tokens.css por fs, en entorno node: vitest
// excluye el CSS del pipeline y un `?raw` llega vacío (medido en
// lightModeOverrides.test.js).
const css = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8')
const temas = parsearTokens(css)
const indexCss = readFileSync(new URL('../index.css', import.meta.url), 'utf8')
const indexHtml = readFileSync(new URL('../../index.html', import.meta.url), 'utf8')

// Contenido de un `@layer base { ... }` de nivel superior, contando llaves (no
// alcanza con una regex no-greedy: el bloque puede tener selectores propios).
// Si no hay `@layer base`, null: eso también es una forma legítima de fallar.
function bloqueLayerBase(hoja) {
  const inicio = hoja.indexOf('@layer base')
  if (inicio === -1) return null
  const abre = hoja.indexOf('{', inicio)
  if (abre === -1) return null
  let profundidad = 0
  for (let i = abre; i < hoja.length; i++) {
    if (hoja[i] === '{') profundidad++
    else if (hoja[i] === '}') {
      profundidad--
      if (profundidad === 0) return hoja.slice(abre + 1, i)
    }
  }
  return null
}

function fallas(tema) {
  return PARES.flatMap(([frente, fondo, minimo]) => {
    const r = contraste(temas[tema][frente], temas[tema][fondo])
    return r >= minimo ? [] : [`${frente} sobre ${fondo} en ${tema}: ${r.toFixed(2)} < ${minimo}`]
  })
}

describe('tokens de color', () => {
  it('tokens.css tiene los dos temas con 48 tokens cada uno', () => {
    expect(Object.keys(temas.oscuro)).toHaveLength(48)
    expect(Object.keys(temas.claro)).toHaveLength(48)
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

  it('los 91 pares alcanzan su mínimo en oscuro', () => {
    expect(PARES).toHaveLength(91)
    expect(fallas('oscuro')).toEqual([])
  })

  it('los 91 pares alcanzan su mínimo en claro', () => {
    expect(fallas('claro')).toEqual([])
  })

  // Task 16b (Ruling 28): "deprecado" necesita su propio token, distinto de
  // "degradado" (aviso). obsoleto es naranja, con pares sobre los tres fondos
  // base -- igual que aviso.
  it('obsoleto existe en TOKENS con pares AA sobre fondo, superficie y hundido', () => {
    expect(TOKENS).toContain('obsoleto')
    for (const fondo of ['fondo', 'superficie', 'hundido']) {
      expect(PARES).toContainEqual(['obsoleto', fondo, AA_TEXTO])
    }
  })

  it('colorToken arma rgb(var()) y un nombre desconocido cae en texto-suave', () => {
    expect(colorToken('faceta-hyde')).toBe('rgb(var(--faceta-hyde) / 1)')
    expect(colorToken('peligro', 0.12)).toBe('rgb(var(--peligro) / 0.12)')
    expect(colorToken('no-existe')).toBe('rgb(var(--texto-suave) / 1)')
  })

  it('tokenDeFaceta traduce la clave del backend y cae en texto-suave si no la conoce', () => {
    expect(tokenDeFaceta('jax_local')).toBe('faceta-jax-local')
    expect(tokenDeFaceta('hyde')).toBe('faceta-hyde')
    expect(tokenDeFaceta('claude')).toBe('texto-suave')
    expect(tokenDeFaceta(undefined)).toBe('texto-suave')
  })
})

// Task 27 (PR 4, cierre del rollout): la capa vieja de index.css ya no existe,
// así que el script en línea de index.html no puede seguir marcando la clase.
describe('index.html sin la capa vieja', () => {
  it('no marca la clase light-mode', () => {
    expect(indexHtml).not.toContain('light-mode')
  })
})

// Uso (spec §6.4): un archivo ya migrado no vuelve a pintar con colores crudos.
// El escaneo cubre todo .js/.jsx no-test bajo src (import.meta.glob abajo);
// no hay lista de "migrados" que mantener.
const fuentes = import.meta.glob(['../**/*.{js,jsx}', '!../**/*.test.{js,jsx}'], {
  query: '?raw', import: 'default', eager: true,
})
const porRuta = Object.fromEntries(Object.entries(fuentes).map(([k, v]) => [k.replace(/^\.\.\//, ''), v]))

const PALETA = 'slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose'
const PROPIEDAD = 'bg|text|border|divide|ring|placeholder|fill|stroke|from|via|to|outline|accent|caret|decoration|shadow'
const PROHIBIDOS = [
  ['clase de paleta de Tailwind', new RegExp(`(?<![\\w-])(?:${PROPIEDAD})-(?:(?:${PALETA})-\\d{2,3}|white|black)(?:\\/\\d+)?(?![\\w-])`, 'g')],
  ['color hal-*', /(?<![\w-])(?:bg|text|border)-hal-[a-z]+/g],
  ['clase de la capa vieja', /light-mode/g],
  ['hex', /#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])/g],
  ['rgb()/rgba() literal', /rgba?\(\s*\d/g],
  ['prefijo dark:', /(?<![\w-])dark:[a-z]/g],
  ['color con nombre en SVG o estilo', /(?:fill|stroke)=["'](?:white|black)["']|:\s*['"](?:white|black)['"]/g],
]

describe('uso de tokens en los archivos migrados', () => {
  it('los archivos migrados pintan sólo con tokens', () => {
    expect(Object.keys(porRuta).length).toBeGreaterThan(40) // verde sobre cero archivos no vale
    const hallazgos = []
    for (const ruta of Object.keys(porRuta)) {
      const codigo = porRuta[ruta]
      if (codigo === undefined) {
        hallazgos.push(`${ruta}: no existe (¿ruta mal escrita?)`)
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
    for (const ruta of Object.keys(porRuta)) {
      for (const [, token] of (porRuta[ruta] || '').matchAll(/(?<![\w-])text-([a-z0-9-]+)/g)) {
        if (!TOKENS.includes(token) || frentes.has(token)) continue
        if (EXENTOS_TEXTO[token]?.archivos.includes(ruta)) continue
        hallazgos.push(`${ruta}: text-${token}`)
      }
    }
    expect(hallazgos).toEqual([])
  })
})

// Hojas de estilo (spec §6.4): ninguna fuera de tokens.css pinta con hex/rgb
// literal. Se leen por fs porque `?raw` de CSS llega vacío en vitest.
describe('uso de tokens en las hojas de estilo', () => {
  it('ninguna hoja de estilo fuera de tokens.css tiene hex ni rgb literal', () => {
    const raiz = new URL('../', import.meta.url)
    const hojas = readdirSync(raiz, { recursive: true })
      .filter((f) => f.endsWith('.css') && f.replaceAll('\\', '/') !== 'tema/tokens.css')
    expect(hojas.length).toBeGreaterThan(1) // index.css y HalEye.css
    const hallazgos = []
    for (const hoja of hojas) {
      const css = readFileSync(new URL(hoja, raiz), 'utf8')
      for (const m of css.match(/#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])|rgba?\(\s*\d/g) || []) hallazgos.push(`${hoja}: «${m}»`)
    }
    expect(hallazgos).toEqual([])
  })
})

// I-1 (revisión final, 2026-09-14): ningún input migrado declaraba color de
// placeholder, así que mandaba el preflight de Tailwind (gray-400, crudo,
// fijo en los dos temas) -- 2,54:1 sobre `hundido` claro, falla AA. Arreglo en
// la raíz: UNA regla en @layer base de index.css con el token texto-tenue.
describe('placeholder por defecto (I-1)', () => {
  it('texto-tenue sobre hundido ya está en PARES (lo exige el fix de I-1)', () => {
    expect(PARES.some(([frente, fondo]) => frente === 'texto-tenue' && fondo === 'hundido')).toBe(true)
  })

  it('index.css fija ::placeholder con var(--texto-tenue) dentro de @layer base', () => {
    const bloque = bloqueLayerBase(indexCss)
    expect(bloque).toBeTruthy()
    expect(bloque).toMatch(/::placeholder[^{}]*\{[^{}]*rgb\(var\(--texto-tenue\)\)[^{}]*\}/)
  })
})
