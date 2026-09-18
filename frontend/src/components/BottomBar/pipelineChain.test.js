import { describe, it, expect } from 'vitest'
import {
  CHAIN_ROLES,
  cleanroomViolationsDePasos,
  facetOptionsFor,
  buildChainSteps,
  cleanroomViolations,
  defaultFacetsByRole,
  ARBITRO_FACETA,
  arbitroViolations,
  seleccionIncluyeArbitro,
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
    // La auditoría recibe también la crítica (paso 2) desde 2026-09-12: sin
    // ella medía contra lo que el plan DECLARABA haber aceptado, no contra lo
    // que la crítica dijo (E2E b2d87971: "no se proporcionó el texto de la
    // crítica original").
    // audit YA NO depende de unify (paso 3, fix bloqueante 2026-09-18): el
    // catálogo real de capability_motor solo admite {thot, ada} para
    // validate_consistency, thot está prohibido (sala limpia del árbitro) y
    // 'ada' es la misma faceta que unify -- ver el comentario de CHAIN_ROLES.
    expect(CHAIN_ROLES.map(r => r.dependsOn)).toEqual([
      [], [0], [0, 1], [1, 2], [3], [0, 2, 4],
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
    // audit depende de produce (índice 4): si se le asigna a mano la MISMA
    // faceta que produce, colisiona -- sin tocar produce, que ya no puede
    // ser 'thot' de prueba porque 'thot' está prohibido categóricamente
    // (ver describe de abajo, no es un caso de cleanroom "normal").
    const facets = { ...defaultFacetsByRole(), audit: 'kimi' }
    const v = cleanroomViolations(facets)
    expect(v).toEqual([{ role: 'audit', facet: 'kimi', dependsOnRole: 'produce' }])
  })

  it('por defecto critica jekyll y audita ada: thot queda reservado al árbitro que agrega el servidor', () => {
    // Al depender de la crítica, crítica y auditoría no pueden compartir
    // faceta (auditoría independiente). 'thot' está prohibido por la sala
    // limpia del árbitro (fix bloqueante 2026-09-18) y el catálogo real de
    // capability_motor solo admite {thot, ada} para validate_consistency
    // (ver CAPS arriba) -- 'ada' es la única opción, y por eso "audit" ya no
    // depende de "unify" (misma faceta 'ada'; ver CHAIN_ROLES).
    const d = defaultFacetsByRole()
    expect(d.critique).toBe('jekyll')
    expect(d.audit).toBe('ada')
  })

  it('si crítica y auditoría son la misma faceta, avisa', () => {
    const v = cleanroomViolations({ ...defaultFacetsByRole(), audit: 'jekyll' })
    expect(v).toEqual([{ role: 'audit', facet: 'jekyll', dependsOnRole: 'critique' }])
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
    // Mide contra la crítica misma, no contra lo que el plan dice de ella.
    expect(es.chainInstructions.audit).toMatch(/crítica original/i)
    expect(en.chainInstructions.audit).toMatch(/original critique/i)
  })
})

describe('sala limpia del árbitro (ARBITRO_FACETA, bloqueante 2026-09-18)', () => {
  it('ARBITRO_FACETA es thot', () => {
    expect(ARBITRO_FACETA).toBe('thot')
  })

  it('la cadena por defecto no usa la faceta árbitro en ningún rol', () => {
    expect(arbitroViolations(defaultFacetsByRole())).toEqual([])
  })

  it('avisa INCONDICIONALMENTE si un rol usa la faceta árbitro como productor, sin importar capability ni dependencia', () => {
    // 'research' no depende de nada y su capability no es de auditoría --
    // el cleanroom viejo lo dejaría pasar. La regla nueva no mira ninguna
    // de las dos cosas: alcanza con que la faceta sea la del árbitro.
    const facets = { ...defaultFacetsByRole(), research: 'thot' }
    expect(cleanroomViolations(facets)).toEqual([])  // el cleanroom viejo no lo ve
    expect(arbitroViolations(facets)).toEqual([{ role: 'research', facet: 'thot' }])
  })

  it('marca cada rol que use la faceta árbitro, no solo el primero', () => {
    const facets = { ...defaultFacetsByRole(), plan: 'thot', produce: 'thot' }
    expect(arbitroViolations(facets)).toEqual([
      { role: 'plan', facet: 'thot' },
      { role: 'produce', facet: 'thot' },
    ])
  })

  it('seleccionIncluyeArbitro: true si la faceta árbitro está entre las elegidas (paralelo)', () => {
    expect(seleccionIncluyeArbitro(['hipatia', 'jekyll', 'thot'])).toBe(true)
    expect(seleccionIncluyeArbitro(['hipatia', 'jekyll', 'jax_local'])).toBe(false)
    expect(seleccionIncluyeArbitro([])).toBe(false)
  })
})

describe('cleanroomViolationsDePasos (continuar, spec 2026-09-17 §6.2)', () => {
  it('marca un paso de auditoría con la misma faceta que una de sus dependencias', () => {
    const pasos = [
      { facet: 'hipatia', capability: 'research', depends_on: [] },
      { facet: 'ada', capability: 'generate', depends_on: [0] },
      { facet: 'ada', capability: 'validate_consistency', depends_on: [0, 1] },
    ]
    expect(cleanroomViolationsDePasos(pasos)).toEqual([{ paso: 2, facet: 'ada', dependsOn: 1 }])
  })

  it('sin capability de auditoría o sin coincidencia no marca nada', () => {
    expect(cleanroomViolationsDePasos([
      { facet: 'ada', capability: 'research', depends_on: [] },
      { facet: 'ada', capability: 'generate', depends_on: [0] },
      { facet: 'thot', capability: 'critique', depends_on: [0, 1] },
    ])).toEqual([])
  })
})
