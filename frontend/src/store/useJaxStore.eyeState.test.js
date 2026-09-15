import { describe, expect, it } from 'vitest'
import { getEyeState } from './useJaxStore'
import { TOKENS } from '../tema/tokens'

describe('getEyeState idle label i18n', () => {
  it('uses the given idleLabel instead of hardcoded Spanish when idle', () => {
    const eye = getEyeState({}, {}, true, false, false, 'idle')
    expect(eye.label).toBe('idle')
  })

  it('falls back to the Spanish default if no idleLabel is passed', () => {
    const eye = getEyeState({}, {}, true, false, false)
    expect(eye.label).toBe('reposo')
  })

  it('idleLabel is ignored for non-idle states (kill switch takes priority)', () => {
    const eye = getEyeState({}, {}, true, true, false, 'idle')
    expect(eye.label).toBe('KILL SWITCH')
  })
})

// M5 (revisión de código, 2026-09-14, fix vivo): las etiquetas visibles de
// getEyeState (KILL SWITCH, DALL-E 3, LAS MANOS DOWN, GATE, Jacobs) venían
// escritas a mano en la función. Ahora son un parámetro `labels` -- quien
// llama las pasa desde i18n (HalEye.jsx lo hace con `t.eye*`); sin el
// parámetro, caen en el mismo texto de siempre (compatibilidad con las
// llamadas de arriba, que no lo pasan).
describe('getEyeState labels (M5, sin hardcodear)', () => {
  it('sin `labels`, usa el texto de siempre (compatibilidad)', () => {
    expect(getEyeState({}, {}, true, true, false).label).toBe('KILL SWITCH')
    expect(getEyeState({}, {}, true, false, true).label).toBe('DALL-E 3')
    expect(getEyeState({}, {}, false, false, false).label).toBe('LAS MANOS DOWN')
    expect(getEyeState({}, { p: { status: 'waiting_gate' } }, true, false, false).label).toBe('GATE')
    expect(getEyeState({}, { p: { status: 'running' } }, true, false, false).label).toBe('Jacobs')
  })

  it('con `labels`, usa exactamente lo que se le pasa -- no el texto hardcodeado', () => {
    const labels = {
      killSwitch: 'INTERRUPTOR', dalle: 'PINTOR-3', lasManosDown: 'MANOS CAÍDAS',
      gate: 'PORTÓN', jacobs: 'Jacobo',
    }
    expect(getEyeState({}, {}, true, true, false, 'idle', labels).label).toBe('INTERRUPTOR')
    expect(getEyeState({}, {}, true, false, true, 'idle', labels).label).toBe('PINTOR-3')
    expect(getEyeState({}, {}, false, false, false, 'idle', labels).label).toBe('MANOS CAÍDAS')
    expect(getEyeState({}, { p: { status: 'waiting_gate' } }, true, false, false, 'idle', labels).label).toBe('PORTÓN')
    expect(getEyeState({}, { p: { status: 'running' } }, true, false, false, 'idle', labels).label).toBe('Jacobo')
  })
})

// Task 20 (spec 2026-09-14-tema-tokens §7.3): el ojo devuelve el NOMBRE de un
// token del tema, no un hex; HalEye lo pinta con colorToken().
describe('getEyeState devuelve tokens', () => {
  it('cada estado del ojo devuelve un token del tema, no un hex', () => {
    const pensando = { hyde: { status: 'thinking', token: 'faceta-hyde' } }
    const casos = [
      getEyeState({}, {}, true, true, false, 'idle'),                                 // kill switch
      getEyeState({}, {}, true, false, true, 'idle'),                                 // DALL·E
      getEyeState(pensando, {}, true, false, false, 'idle'),                          // faceta pensando
      getEyeState({}, {}, false, false, false, 'idle'),                               // LAS MANOS caído
      getEyeState({}, { p: { status: 'waiting_gate' } }, true, false, false, 'idle'), // gate
      getEyeState({}, { p: { status: 'running' } }, true, false, false, 'idle'),      // Jacobs
      getEyeState({}, {}, true, false, false, 'idle'),                                // reposo
    ]
    expect(casos.map((e) => e.token)).toEqual([
      'peligro', 'faceta-imagen', 'faceta-hyde', 'texto-tenue', 'aviso', 'faceta-jacobs', 'faceta-jax-local',
    ])
    for (const e of casos) {
      expect(TOKENS).toContain(e.token)
      expect(e).not.toHaveProperty('color')
    }
  })
})
