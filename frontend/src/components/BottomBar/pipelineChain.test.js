import { describe, it, expect } from 'vitest'
import {
  CHAIN_ROLES,
  facetOptionsFor,
  buildChainSteps,
  cleanroomViolations,
  defaultFacetsByRole,
} from './pipelineChain'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

// Catálogo con la forma real de GET /api/motors/capabilities (capability_motor).
const CAPS = {
  research: [],
  design: ['ada', 'kimi', 'jax_local'],
  critique: ['thot', 'ada'],
  reconcile: ['ada', 'kimi', 'jax_local'],
  generate: ['kimi', 'ada', 'jax_local'],
  validate_consistency: ['thot', 'ada'],
}

const INSTRUCTIONS = {
  research: 'INVESTIGA', plan: 'PLANIFICA', critique: 'CRITICA',
  unify: 'UNIFICA', produce: 'PRODUCE', audit: 'AUDITA',
}

describe('pipelineChain -- la cadena en línea', () => {
  it('seis roles en el orden pedido: investigar, planificar, criticar, unificar, producir, auditar', () => {
    expect(CHAIN_ROLES.map(r => r.capability)).toEqual([
      'research', 'design', 'critique', 'reconcile', 'generate', 'validate_consistency',
    ])
  })

  it('cada paso depende solo de lo que necesita, y siempre de pasos anteriores', () => {
    // Contexto mínimo: cada dependencia reenvía hasta 60.000 caracteres al
    // modelo, y eso es dinero en cada llamada.
    expect(CHAIN_ROLES.map(r => r.dependsOn)).toEqual([
      [], [0], [0, 1], [1, 2], [3], [0, 3, 4],
    ])
    CHAIN_ROLES.forEach((r, i) => r.dependsOn.forEach(d => expect(d).toBeLessThan(i)))
  })

  it('arma un step por rol, con depends_on y sin timeout_seconds (lo pone la DB)', () => {
    const steps = buildChainSteps('un ERP', defaultFacetsByRole(), INSTRUCTIONS)
    expect(steps).toHaveLength(6)
    steps.forEach((s, i) => {
      expect(s.capability).toBe(CHAIN_ROLES[i].capability)
      expect(s.depends_on).toEqual(CHAIN_ROLES[i].dependsOn)
      expect(s).not.toHaveProperty('timeout_seconds')
      expect(s.skip_on_fail).toBe(false)
      expect(s.prompt).toContain('un ERP')
    })
    expect(steps[0].prompt).toContain('INVESTIGA')
    expect(steps[5].prompt).toContain('AUDITA')
  })

  it('fija motor=faceta para kimi/jax_local y no manda motor para las facetas HTTP', () => {
    const steps = buildChainSteps('x', defaultFacetsByRole(), INSTRUCTIONS)
    expect(steps[4]).toMatchObject({ facet: 'kimi', motor: 'kimi' })
    expect(steps[0]).not.toHaveProperty('motor')  // hipatia
    expect(steps[1]).not.toHaveProperty('motor')  // ada
  })

  it('ofrece motores solo donde capability_motor los permite; las HTTP siempre', () => {
    const research = facetOptionsFor(CHAIN_ROLES[0], CAPS)
    expect(research).not.toContain('kimi')
    expect(research).not.toContain('jax_local')
    expect(research).toContain('hipatia')
    expect(facetOptionsFor(CHAIN_ROLES[4], CAPS)).toEqual(
      expect.arrayContaining(['kimi', 'jax_local', 'ada']),
    )
    expect(facetOptionsFor(CHAIN_ROLES[2], CAPS)).not.toContain('kimi')  // critique
  })

  it('la cadena por defecto respeta la auditoría independiente', () => {
    expect(cleanroomViolations(defaultFacetsByRole())).toEqual([])
  })

  it('avisa si el auditor es la misma faceta que produjo algo que audita', () => {
    const facets = { ...defaultFacetsByRole(), produce: 'thot' }
    const v = cleanroomViolations(facets)
    expect(v).toEqual([{ role: 'audit', facet: 'thot', dependsOnRole: 'produce' }])
  })

  it('las instrucciones existen en los dos idiomas para cada rol', () => {
    for (const dict of [es, en]) {
      for (const role of CHAIN_ROLES) {
        expect(dict.chainInstructions[role.id], role.id).toBeTruthy()
        expect(dict.chainRoles[role.id], role.id).toBeTruthy()
      }
    }
  })

  it('la auditoría toma como verdad solo la investigación y mide qué aportó la crítica', () => {
    // Blueprint de Ricardo §7.3: si el auditor valida contra lo que produjeron
    // los otros pasos, un invento del plan que la producción repite pasa limpio.
    // §12: sin medir si la crítica cambió algo, no se sabe si la cadena vale
    // lo que cuesta.
    expect(es.chainInstructions.audit).toMatch(/investigaci/i)
    expect(es.chainInstructions.audit).toMatch(/crítica/i)
    expect(en.chainInstructions.audit).toMatch(/research/i)
    expect(en.chainInstructions.audit).toMatch(/critique/i)
  })
})
