// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'

// Ronda de arreglo 2 (2026-09-18, Fernando probó el Historial con un pipeline
// real de 7 pasos): `body { overflow: hidden }` en index.css es GLOBAL --
// correcto para la Mesa (Dashboard.jsx) y Admin (Admin.jsx), que son layouts
// fijos de un viewport con su propio scroll interno, pero hace IMPOSIBLE leer
// el Historial, que es un documento largo (con 7 pasos no hay forma de llegar
// al final). La regla no es de `body`: es de las DOS pantallas que la
// necesitan, y las dos YA se declaran a sí mismas con altura fija + su propio
// overflow -- Dashboard.jsx:14 (`h-dvh ... overflow-hidden`) y Admin.jsx:14/18
// (`h-dvh` + `overflow-y-auto` en <main>). Sacar la regla del body no les
// cambia nada: siguen conteniéndose solas. Historial.jsx usa `min-h-dvh` (sin
// overflow-hidden): un documento que crece con el contenido, como Login y
// ResetPassword.
//
// Qué SÍ prueba este test: que la fuente de las pantallas declara el
// contrato correcto (dónde vive cada regla). Qué NO prueba: que un usuario
// real pueda scrollear con el mouse -- jsdom no hace layout ni aplica
// index.css (no hay bundler/PostCSS en el test), así que ninguna aserción de
// DOM puede demostrar eso. Esa parte se verificó a mano con el dev server
// (ver REPORTE.md).
const indexCss = readFileSync(new URL('./index.css', import.meta.url), 'utf8')
const dashboardSrc = readFileSync(new URL('./pages/Dashboard.jsx', import.meta.url), 'utf8')
const adminSrc = readFileSync(new URL('./pages/Admin.jsx', import.meta.url), 'utf8')
const historialSrc = readFileSync(new URL('./pages/Historial.jsx', import.meta.url), 'utf8')

// Extrae el contenido de un selector top-level `selector { ... }`, contando
// llaves (mismo criterio que bloqueLayerBase en tema/contraste.test.js) --
// una regex no-greedy no alcanza si el valor tuviera llaves propias.
function bloqueDeSelector(hoja, selector) {
  const re = new RegExp(`(^|\\n)\\s*${selector}\\s*\\{`)
  const m = hoja.match(re)
  if (!m) return null
  const abre = hoja.indexOf('{', m.index)
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

describe('overflow: dónde vive la regla (ronda de arreglo 2, Historial ilegible con 7 pasos)', () => {
  it('el body global YA NO fuerza overflow: hidden -- un documento largo (Historial) tiene que poder crecer', () => {
    const bloqueBody = bloqueDeSelector(indexCss, 'body')
    expect(bloqueBody).not.toBeNull()
    expect(bloqueBody).not.toMatch(/overflow\s*:\s*hidden/)
  })

  it('la Mesa (Dashboard) se sigue conteniendo sola: h-dvh + overflow-hidden en su propia raíz', () => {
    expect(dashboardSrc).toMatch(/className="flex flex-col h-dvh[^"]*overflow-hidden[^"]*"/)
  })

  it('Admin se sigue conteniendo solo: h-dvh en la raíz, overflow-y-auto en el <main> que scrollea', () => {
    expect(adminSrc).toMatch(/className="flex h-dvh[^"]*"/)
    expect(adminSrc).toMatch(/<main[^>]*overflow-y-auto/)
  })

  it('Historial es un documento que crece con el contenido: min-h-dvh en la raíz, sin overflow-hidden ahí (la tabla sí lo usa, para las esquinas redondeadas, no para cortar la página)', () => {
    const raiz = historialSrc.match(/className="(min-h-dvh[^"]*)"/)
    expect(raiz).not.toBeNull()
    expect(raiz[1]).not.toMatch(/overflow-hidden/)
  })
})
