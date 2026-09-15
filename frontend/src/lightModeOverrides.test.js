// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'

// Por fs y no con `?raw`: vitest excluye el CSS del pipeline y el import
// llega vacío (medido: el test fallaba con los overrides ya escritos).
// Entorno node (este test no usa el DOM): import.meta.url es file: y la ruta
// sale relativa a ESTE archivo, no al directorio desde el que se corre.
const css = readFileSync(new URL('./index.css', import.meta.url), 'utf8')

// Modo claro (2026-09-13, fix de la revisión final): los rojos y verdes
// "tintados" del modo oscuro (texto 200-500, fondos 900/950 translúcidos,
// bordes 700/800) no tenían override en html.light-mode -- el aviso de SMTP
// corrupto medía 1,49:1. Este test exige que TODA clase de ese grupo que la
// app usa tenga su override, para que una pantalla nueva no vuelva a quedar
// ilegible en claro. Los fondos sólidos con texto blanco se leen en ambos
// modos y no se sobrescriben.
const SOLIDOS = new Set([
  'bg-green-400', 'bg-red-400', 'bg-red-500', 'bg-red-600',
  'hover:bg-red-500', 'hover:bg-red-700',
  'border-green-600', 'border-red-500', 'border-red-600', 'hover:border-red-600',
])

const fuentes = import.meta.glob(['./**/*.{js,jsx}', '!./**/*.test.{js,jsx}'], {
  query: '?raw', import: 'default', eager: true,
})

function clasesUsadas() {
  const patron = /(?:hover:)?(?:text|bg|border)-(?:red|green)-[0-9]{3}(?:\/[0-9]+)?/g
  const clases = new Set()
  for (const codigo of Object.values(fuentes)) {
    for (const c of codigo.match(patron) || []) clases.add(c)
  }
  return [...clases].filter((c) => !SOLIDOS.has(c)).sort()
}

function selector(clase) {
  const escapada = clase.replace(/:/g, '\\:').replace(/\//g, '\\/')
  return `html.light-mode .${escapada}${clase.startsWith('hover:') ? ':hover' : ''}`
}

describe('modo claro: rojos y verdes tintados', () => {
  it('cada clase tintada que usa la app tiene override en html.light-mode', () => {
    const usadas = clasesUsadas()
    // Piso bajado de > 10 a > 0 (Task 21 del tema con tokens, 2026-09-15): la
    // migración del PR 3 deja menos rojos y verdes tintados en uso, y cada
    // tarea del chat baja la cuenta. El test se borra en la Task 24 del plan,
    // junto con los rojos y verdes de la capa vieja. La aserción que manda
    // (toda clase en uso tiene override) no cambia.
    expect(usadas.length).toBeGreaterThan(0) // el escaneo encontró algo de verdad
    const faltan = usadas.filter((c) => !css.includes(`${selector(c)} {`))
    expect(faltan).toEqual([])
  })
})
