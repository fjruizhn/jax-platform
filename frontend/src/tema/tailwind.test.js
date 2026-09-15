// @vitest-environment node
import { it, expect } from 'vitest'
import config from '../../tailwind.config.js'
import { TOKENS } from './tokens.js'

// Cada token es un color de Tailwind con opacidad, leído de la variable CSS.
// Si alguien agrega un token a tokens.js y no llega a Tailwind, esto falla.
it('tailwind.config.js expone cada token como rgb(var(--x) / <alpha-value>)', () => {
  const colores = config.theme.extend.colors
  const mal = TOKENS.filter((n) => colores[n] !== `rgb(var(--${n}) / <alpha-value>)`)
  expect(mal).toEqual([])
  // El `oro` de antes era un objeto con hex; ahora es el token.
  expect(colores.oro).toBe('rgb(var(--oro) / <alpha-value>)')
  // Task 27 (PR 4, cierre del rollout): Dashboard migró en el Task 26; los
  // colores hal-* de la capa vieja ya no tienen usuarios.
  expect(colores.hal).toBeUndefined()
})
