// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'

// WCAG 2.2 §2.5.8 (Target Size · Minimum): el área de un control interactivo
// no puede ser menor a 24×24px. El patrón `text-xs px-2 py-1`, solo, mide
// ~20px de alto -- bajo el mínimo (medido 2026-09-21: 16 usos en 6 archivos
// de botones de acción en Memoria y en los admin de modelos).
//
// El arreglo centraliza el tamaño en tema/botones.js (TAMANO_BOTON_ACCION,
// que agrega min-h-6 = 24px) y lo usan los seis archivos en vez de repetir
// el literal. Este detector cierra la puerta a que vuelva a aparecer suelto:
// el patrón viejo solo puede vivir en su propia definición. Mismo patrón que
// politica/modales.test.js (un componente concentra lo que antes se repetía).
const raiz = new URL('../', import.meta.url)
const archivos = readdirSync(raiz, { recursive: true })
  .filter((r) => /\.(jsx|js)$/.test(r) && !/\.test\./.test(r))

const DEFINICION = 'tema/botones.js'
const PATRON = 'text-xs px-2 py-1'

describe('tamaño mínimo de toque (WCAG 2.2 2.5.8)', () => {
  it('el patrón bajo el mínimo de 24px solo vive en su propia definición', () => {
    expect(archivos.length).toBeGreaterThan(40) // verde sobre cero archivos no vale
    const conElPatron = archivos.filter((r) => readFileSync(new URL(r, raiz), 'utf8').includes(PATRON))
    expect(conElPatron).toEqual([DEFINICION])
  })
})
