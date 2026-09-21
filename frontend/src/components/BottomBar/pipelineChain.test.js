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
  file_read: ['jax_local', 'kimi'],
  research: [],
  design: ['ada', 'kimi', 'jax_local'],
  critique: ['thot', 'ada'],
  reconcile: ['ada', 'kimi', 'jax_local'],
  generate: ['kimi', 'ada', 'jax_local'],
  validate_consistency: ['thot', 'ada'],
}

const INSTRUCTIONS = {
  read: 'LEE', research: 'INVESTIGA', plan: 'PLANIFICA', critique: 'CRITICA',
  unify: 'UNIFICA', produce: 'PRODUCE',
}

describe('pipelineChain -- la cadena en línea', () => {
  it('cinco roles en el orden pedido: investigar, planificar, criticar, unificar, producir', () => {
    // Ronda de arreglo (2026-09-18): "audit" se sacó de la cadena -- el
    // árbitro que Jacobs agrega SOLO al final de cualquier plan de 2+ pasos
    // (jacobs/plan.py::_con_arbitro) depende de TODOS los pasos y hace
    // estrictamente más de lo que "audit" hacía (que sólo veía tres de
    // cinco). Ver el comentario completo sobre CHAIN_ROLES en pipelineChain.js.
    // `file_read` al frente desde 2026-09-20: sin él la cadena no podía leer
    // los archivos que el objetivo nombra, y los cinco pasos siguientes
    // construían sobre documentos que nadie abrió. Ver CHAIN_ROLES.
    expect(CHAIN_ROLES.map(r => r.capability)).toEqual([
      'file_read', 'research', 'design', 'critique', 'reconcile', 'generate',
    ])
  })

  it('cada paso depende solo de lo que necesita, y siempre de pasos anteriores', () => {
    // Contexto mínimo: cada dependencia reenvía hasta 60.000 caracteres al
    // modelo, y eso es dinero en cada llamada.
    // La reconciliación ("unify") recibe también la crítica (paso 2) desde
    // 2026-09-12: sin ella medía contra lo que el plan DECLARABA haber
    // aceptado, no contra lo que la crítica dijo (E2E b2d87971: "no se
    // proporcionó el texto de la crítica original").
    // Corrido un índice desde 2026-09-20: `read` entró al frente y todos los
    // demás pasan a depender también de él (paso 0), que es quien trae el
    // contenido real de los archivos del objetivo.
    expect(CHAIN_ROLES.map(r => r.dependsOn)).toEqual([
      [], [0], [0, 1], [1, 2], [2, 3], [4],
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
    expect(steps[0].prompt).toContain('LEE')
    expect(steps[1].prompt).toContain('INVESTIGA')
    expect(steps[5].prompt).toContain('PRODUCE')
  })

  it('fija motor=faceta para kimi/jax_local y no manda motor para las facetas HTTP', () => {
    const steps = buildChainSteps('x', defaultFacetsByRole(), INSTRUCTIONS)
    expect(steps[5]).toMatchObject({ facet: 'kimi', motor: 'kimi' })
    // `read` va a jax_local, que es motor gobernado: lleva motor explícito.
    expect(steps[0]).toMatchObject({ facet: 'jax_local', motor: 'jax_local' })
    expect(steps[1]).not.toHaveProperty('motor')  // hipatia
    expect(steps[1]).not.toHaveProperty('motor')  // ada
  })

  it('ofrece motores solo donde capability_motor los permite; las HTTP siempre', () => {
    const research = facetOptionsFor(CHAIN_ROLES[1], CAPS)
    expect(research).not.toContain('kimi')
    expect(research).not.toContain('jax_local')
    expect(research).toContain('hipatia')
    expect(facetOptionsFor(CHAIN_ROLES[5], CAPS)).toEqual(
      expect.arrayContaining(['kimi', 'jax_local', 'ada']),
    )
    expect(facetOptionsFor(CHAIN_ROLES[3], CAPS)).not.toContain('kimi')  // critique
  })

  it('la cadena por defecto no tiene ningún rol que comparta faceta con una dependencia directa', () => {
    expect(cleanroomViolations(defaultFacetsByRole())).toEqual([])
  })

  it('avisa si un rol es la misma faceta que produjo algo de lo que depende (cleanroom)', () => {
    // "critique" depende de "plan" (índice 1, faceta 'ada'): si se le asigna
    // a mano la MISMA faceta, colisiona. 'critique' tiene capability
    // 'critique', que está en AUDIT_CAPABILITIES -- sigue siendo un caso
    // real, aunque ya no sea el rol "audit" (que se sacó de la cadena).
    const facets = { ...defaultFacetsByRole(), critique: 'ada' }
    const v = cleanroomViolations(facets)
    expect(v).toEqual([{ role: 'critique', facet: 'ada', dependsOnRole: 'plan' }])
  })

  it('las instrucciones existen en los dos idiomas para cada rol', () => {
    for (const dict of [es, en]) {
      for (const role of CHAIN_ROLES) {
        expect(dict.chainInstructions[role.id], role.id).toBeTruthy()
        expect(dict.chainRoles[role.id], role.id).toBeTruthy()
      }
    }
  })

  // Ronda de arreglo (2026-09-18), pedido explícito: un test que cubra que
  // la cadena por defecto se crea SIN que el servidor la rechace. Es el que
  // hubiera cazado el bloqueante de entrada (thot como "audit" por
  // defecto, auto-rechazado por la sala limpia del árbitro) antes de que
  // llegara a producción.
  it('la cadena por defecto no dispara ningún rechazo: ni cleanroom, ni la sala limpia del árbitro, y cada faceta default es válida contra el catálogo real', () => {
    const defaults = defaultFacetsByRole()
    expect(cleanroomViolations(defaults)).toEqual([])
    expect(arbitroViolations(defaults)).toEqual([])
    CHAIN_ROLES.forEach((role) => {
      expect(facetOptionsFor(role, CAPS)).toContain(defaults[role.id])
    })
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
