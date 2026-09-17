// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'

// A-23 (2026-09-16): un modal hecho a mano (velo `fixed inset-0` + panel) no
// tiene inert, foco, Escape ni ARIA (Ruling U27). Todo modal va sobre
// components/Dialogo.jsx, el único que puede dibujar el velo.
const raiz = new URL('../', import.meta.url)
const archivos = readdirSync(raiz, { recursive: true })
  .filter((r) => /\.(jsx|js)$/.test(r) && !/\.test\./.test(r))

describe('modales', () => {
  it('solo Dialogo.jsx dibuja el velo de un modal', () => {
    expect(archivos.length).toBeGreaterThan(40) // verde sobre cero archivos no vale
    const conVelo = archivos.filter((r) => readFileSync(new URL(r, raiz), 'utf8').includes('fixed inset-0'))
    expect(conVelo).toEqual(['components/Dialogo.jsx'])
  })
})
