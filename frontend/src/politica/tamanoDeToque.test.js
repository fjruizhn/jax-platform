// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'

// WCAG 2.2 §2.5.8 (Target Size · Minimum): el área de un control interactivo
// no puede ser menor a 24×24px. El patrón `text-xs px-2 py-1` aparecía
// suelto en 16 usos de 6 archivos de botones de acción en Memoria y en los
// admin de modelos (2026-09-21).
//
// CORRECCIÓN (2026-09-21, revisión adversarial): esta nota decía antes que
// el patrón medía "~20px de alto, bajo el mínimo (medido...)". Esa cifra
// nunca se midió -- era una estimación a ojo repetida como si fuera un
// hecho verificado. Medido de verdad con Chrome real vía CDP (Principio I,
// ver tema/botones.js): el patrón mide exactamente 24.00px, EN el mínimo,
// no por debajo. El arreglo de abajo sigue siendo correcto igual -- 24.00px
// justo en el borde es un valor sin margen (medio pixel de redondeo, un
// cambio de fuente, cae por debajo sin que nadie lo note), y el problema de
// ANCHO que motivó `min-w-6` en TAMANO_BOTON_ACCION es real e independiente
// de esta cifra. Se corrige el "medido" falso, no el resultado del arreglo.
//
// El arreglo centraliza el tamaño en tema/botones.js (TAMANO_BOTON_ACCION,
// que agrega min-h-6/min-w-6) y lo usan los seis archivos en vez de repetir
// el literal. Este detector cierra la puerta a que vuelva a aparecer suelto:
// el patrón viejo solo puede vivir en su propia definición. Mismo patrón que
// politica/modales.test.js (un componente concentra lo que antes se repetía).
//
// Límite conocido: sólo cubre ESTE literal exacto. La familia
// `text-xs ... py-0.5` (20px medido, genuinamente bajo el mínimo, no un
// caso límite) es un patrón DISTINTO -- ver
// politica/botonesConPocoRelleno.test.js.
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
