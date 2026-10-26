// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { temaInicial, aplicarTema, aplicarInstalacion } from './aplicarTema.js'

// El script en línea de index.html es la copia mínima de temaInicial +
// aplicarTema (spec §5.3) y de la detección de instalación de oficina
// (OP 358 pieza 2): corre antes del primer pintado y no puede importar
// módulos. Este test lo extrae y lo corre con un DOM, un localStorage y un
// location falsos, y exige que deje el MISMO DOM que el módulo, en cada
// combinación.
const html = readFileSync(new URL('../../index.html', import.meta.url), 'utf8')
const script = html.match(/<script id="tema-inicial">([\s\S]*?)<\/script>/)?.[1]

function almacen(valores) {
  return { getItem: (k) => (k in valores ? valores[k] : null) }
}

// M-4 (revisión final, 2026-09-14): con el almacenamiento bloqueado (Safari
// con cookies bloqueadas, iframe con sandbox), getItem lanza SecurityError.
function almacenQueLanza() {
  return {
    getItem: () => { throw new DOMException('bloqueado', 'SecurityError') },
  }
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

// Instalación de la oficina (OP 358 pieza 2): el script decide
// data-instalacion="oficina" leyendo el CSV %VITE_OFFICE_HOSTS% (Vite lo
// reemplaza al construir; en el build de la Mesa queda el literal, que no
// casa con ningún host real). Aquí se sustituye el CSV a mano y se compara
// contra aplicarInstalacion del módulo, igual que con el tema. `location` se
// pasa como parámetro de new Function para controlar el hostname sin DOM.
const CASOS_OFICINA = [
  ['host de oficina en el CSV', 'virtualoffice.axioma-ia.io', 'virtualoffice.axioma-ia.io', 'oficina'],
  ['host ajeno al CSV', 'virtualoffice.axioma-ia.io', 'app.axioma-ia.io', undefined],
  ['CSV con espacios y mayúsculas', ' VirtualOffice.Axioma-IA.io , otro.ejemplo.com ', 'virtualoffice.axioma-ia.io', 'oficina'],
  ['CSV vacío', '', 'virtualoffice.axioma-ia.io', undefined],
  ['CSV sin reemplazar (build de la Mesa)', '%VITE_OFFICE_HOSTS%', 'virtualoffice.axioma-ia.io', undefined],
  ['hostname vacío', 'virtualoffice.axioma-ia.io', '', undefined],
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
    new Function('localStorage', 'document', 'location', script)(almacen(valores), { documentElement: deScript })
    const deModulo = raizFalsa()
    aplicarTema(temaInicial(almacen(valores)), deModulo)
    // Sin location (tercer argumento ausente) el script no marca instalación;
    // el módulo con hostname desconocido tampoco.
    aplicarInstalacion(undefined, undefined, deModulo)
    expect(foto(deScript)).toEqual(foto(deModulo))
    // Que los dos coincidan no alcanza: tienen que coincidir en lo correcto.
    expect(deModulo.attrs['data-tema']).toBe(esperado === 'light' ? 'claro' : undefined)
  })

  it.each(CASOS_OFICINA)('instalación %s: el script y el módulo dejan el mismo DOM', (_, csv, host, esperado) => {
    const scriptConCsv = script.replaceAll('%VITE_OFFICE_HOSTS%', csv)
    const deScript = raizFalsa()
    new Function('localStorage', 'document', 'location', scriptConCsv)(
      almacen({}), { documentElement: deScript }, { hostname: host },
    )
    const deModulo = raizFalsa()
    aplicarTema(temaInicial(almacen({})), deModulo)
    aplicarInstalacion(host, csv, deModulo)
    expect(foto(deScript)).toEqual(foto(deModulo))
    // Ídem: coincidir no alcanza, tienen que acertar.
    expect(deModulo.attrs['data-instalacion']).toBe(esperado)
  })

  it('con localStorage bloqueado, el script y aplicarTema caen los dos a oscuro sin lanzar', () => {
    const deScript = raizFalsa()
    expect(() => {
      new Function('localStorage', 'document', 'location', script)(almacenQueLanza(), { documentElement: deScript })
    }).not.toThrow()
    const deModulo = raizFalsa()
    expect(() => {
      aplicarTema(temaInicial(almacenQueLanza()), deModulo)
      aplicarInstalacion(undefined, undefined, deModulo)
    }).not.toThrow()
    expect(foto(deScript)).toEqual(foto(deModulo))
    expect(deModulo.attrs['data-tema']).toBeUndefined()
    expect(deModulo.attrs['data-instalacion']).toBeUndefined()
  })
})
