// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { temaInicial, aplicarTema } from './aplicarTema.js'

// El script en línea de index.html es la copia mínima de temaInicial +
// aplicarTema (spec §5.3): corre antes del primer pintado y no puede importar
// módulos. Este test lo extrae y lo corre con un DOM y un localStorage falsos,
// y exige que deje el MISMO DOM que el módulo, en cada combinación.
const html = readFileSync(new URL('../../index.html', import.meta.url), 'utf8')
const script = html.match(/<script id="tema-inicial">([\s\S]*?)<\/script>/)?.[1]

function almacen(valores) {
  return { getItem: (k) => (k in valores ? valores[k] : null) }
}

function raizFalsa() {
  const attrs = {}
  const clases = new Set()
  return {
    attrs, clases,
    setAttribute: (k, v) => { attrs[k] = v },
    removeAttribute: (k) => { delete attrs[k] },
    classList: { add: (c) => clases.add(c), remove: (c) => clases.delete(c) },
  }
}

const foto = (r) => ({ attrs: { ...r.attrs }, clases: [...r.clases].sort() })

const CASOS = [
  ['sin elección ni predeterminado', {}, 'dark'],
  ['elección claro', { jax_theme: 'light' }, 'light'],
  ['sin elección, predeterminado claro', { jax_theme_default: 'light' }, 'light'],
  ['elección oscuro gana al predeterminado claro', { jax_theme: 'dark', jax_theme_default: 'light' }, 'dark'],
  ['elección inválida cae al predeterminado', { jax_theme: 'violeta', jax_theme_default: 'light' }, 'light'],
]

describe('script de tema en index.html', () => {
  // M-2 (revisión final, 2026-09-14): el chequeo original sólo buscaba
  // `<script type="module"` DENTRO de <head>, pero el módulo vive en <body>
  // -- esa rama nunca corría y el test aparentaba cubrir el orden sin
  // hacerlo. Ahora se ubica el bloque del script de tema (dondequiera que
  // esté) y se exige que esté en <head> y que NADA de <link>, <style> o
  // <script> (cualquier otro, en TODO el documento, head o body) aparezca
  // antes de que termine ese bloque.
  it('existe, está en <head>, y va antes de cualquier link, style u otro script', () => {
    expect(script).toBeTruthy()
    const head = html.slice(0, html.indexOf('</head>'))
    expect(head).toContain('id="tema-inicial"')

    const inicio = html.indexOf('<script id="tema-inicial">')
    expect(inicio).not.toBe(-1)
    const finBloque = html.indexOf('</script>', inicio) + '</script>'.length
    const antes = html.slice(0, inicio)
    const despues = html.slice(finBloque)

    for (const tag of ['<link', '<style', '<script']) {
      expect(antes.includes(tag)).toBe(false)
    }
    // Si nada de esto aparece DESPUÉS en todo el documento, la comprobación de
    // arriba es una tautología (verde sobre un documento sin recursos que
    // comparar): el módulo de <body> tiene que aparecer ahí para que el
    // chequeo de orden pruebe algo real.
    expect(despues).toContain('<script type="module"')
  })

  it.each(CASOS)('%s: el script y aplicarTema dejan el mismo DOM', (_, valores, esperado) => {
    const deScript = raizFalsa()
    new Function('localStorage', 'document', script)(almacen(valores), { documentElement: deScript })
    const deModulo = raizFalsa()
    aplicarTema(temaInicial(almacen(valores)), deModulo)
    expect(foto(deScript)).toEqual(foto(deModulo))
    // Que los dos coincidan no alcanza: tienen que coincidir en lo correcto.
    expect(deModulo.attrs['data-tema']).toBe(esperado === 'light' ? 'claro' : undefined)
  })
})
