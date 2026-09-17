import { describe, expect, it } from 'vitest'
import { getEyeState } from './useJaxStore'
import { TOKENS } from '../tema/tokens'

const ETIQUETAS = {
  reposo: 'idle', killSwitch: 'INTERRUPTOR', dalle: 'PINTOR-3', lasManosDown: 'MANOS CAÍDAS', gate: 'PORTÓN', jacobs: 'Jacobo',
}

// A-45 (2026-09-16): sin defaults. Las etiquetas vienen siempre de quien llama.
describe('getEyeState usa exactamente las etiquetas que recibe', () => {
  it('cada estado devuelve su etiqueta', () => {
    expect(getEyeState({}, {}, true, false, false, ETIQUETAS).label).toBe('idle')
    expect(getEyeState({}, {}, true, true, false, ETIQUETAS).label).toBe('INTERRUPTOR')
    expect(getEyeState({}, {}, true, false, true, ETIQUETAS).label).toBe('PINTOR-3')
    expect(getEyeState({}, {}, false, false, false, ETIQUETAS).label).toBe('MANOS CAÍDAS')
    expect(getEyeState({}, { p: { status: 'waiting_gate' } }, true, false, false, ETIQUETAS).label).toBe('PORTÓN')
    expect(getEyeState({}, { p: { status: 'running' } }, true, false, false, ETIQUETAS).label).toBe('Jacobo')
  })

  it('el kill switch tiene prioridad sobre el reposo', () => {
    expect(getEyeState({}, {}, true, true, false, ETIQUETAS).label).toBe('INTERRUPTOR')
  })
})

// Task 20 (spec 2026-09-14-tema-tokens §7.3): el ojo devuelve el NOMBRE de un token del tema, no un hex.
describe('getEyeState devuelve tokens', () => {
  it('cada estado del ojo devuelve un token del tema, no un hex', () => {
    const pensando = { hyde: { status: 'thinking', token: 'faceta-hyde' } }
    const casos = [
      getEyeState({}, {}, true, true, false, ETIQUETAS),
      getEyeState({}, {}, true, false, true, ETIQUETAS),
      getEyeState(pensando, {}, true, false, false, ETIQUETAS),
      getEyeState({}, {}, false, false, false, ETIQUETAS),
      getEyeState({}, { p: { status: 'waiting_gate' } }, true, false, false, ETIQUETAS),
      getEyeState({}, { p: { status: 'running' } }, true, false, false, ETIQUETAS),
      getEyeState({}, {}, true, false, false, ETIQUETAS),
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
