// Cadena en línea para Jacobs: investigar -> maquetar/planificar -> criticar
// -> unificar -> producir -> auditar. Pedido de Fernando, 2026-09-12: el
// modal armaba todos los pasos en paralelo (sin depends_on), así que cada
// faceta trabajaba sobre el objetivo a ciegas y nadie leía lo del otro.
//
// El ejecutor de Jacobs ya ordena por depends_on (olas topológicas) y le
// pasa a cada paso la salida COMPLETA de sus dependencias (hasta 60.000
// caracteres cada una, jacobs/executor.py::_build_context_input) -- tanto a
// los facets de motor como a los HTTP directos. Acá solo se arma el plan.

// Particiones de facetas, espejo de jax/jacobs/models.py. No hay endpoint que
// las exponga (viven en repos distintos): deuda declarada desde T5
// (2026-08-22). Una sola copia en el frontend, acá -- PipelineModal la usa.
// Motor Registry: se valida contra capability_motor y llevan motor fijo.
export const GOVERNED_FACETS = ['jax_local', 'kimi']
// HTTP directo: su admisión mira la capability (allowed_callers), no un
// binding de motor -- jacobs/executor.py::validate_capability, NIVEL C.
export const HTTP_FACETS = ['hipatia', 'jekyll', 'thot', 'ada']

// Espejo de jacobs/plan.py::_AUDIT_CAPABILITIES (las que usa la cadena).
const AUDIT_CAPABILITIES = new Set(['critique', 'validate_consistency'])

// dependsOn es el contexto MÍNIMO de cada paso: cada dependencia reenvía su
// salida entera al modelo, y eso se paga en cada llamada (blueprint de
// Ricardo §9/§11). La auditoría recibe la investigación (su única fuente de
// verdad), la crítica, el plan unificado y el producto -- no el borrador.
//
// La crítica entra a la auditoría desde 2026-09-12 (decisión de Fernando):
// sin ella el auditor medía contra lo que el plan DECLARABA haber aceptado,
// no contra lo que la crítica dijo (E2E b2d87971). Consecuencia: crítica y
// auditoría ya no pueden ser la misma faceta (auditoría independiente), así
// que por defecto critica jekyll y audita thot.
export const CHAIN_ROLES = [
  { id: 'research', capability: 'research',             defaultFacet: 'hipatia', dependsOn: [] },
  { id: 'plan',     capability: 'design',               defaultFacet: 'ada',     dependsOn: [0] },
  { id: 'critique', capability: 'critique',             defaultFacet: 'jekyll',  dependsOn: [0, 1] },
  { id: 'unify',    capability: 'reconcile',            defaultFacet: 'ada',     dependsOn: [1, 2] },
  { id: 'produce',  capability: 'generate',             defaultFacet: 'kimi',    dependsOn: [3] },
  { id: 'audit',    capability: 'validate_consistency', defaultFacet: 'thot',    dependsOn: [0, 2, 3, 4] },
]

// Modo por defecto según la forma (decisión de Fernando, 2026-09-12). En
// `supervised` Jacobs corre UNA ola y pausa (executor.py): en paralelo eso es
// una pausa, en cadena una por paso -- cinco aprobaciones por corrida. La
// cadena corre sola de punta a punta; paralelo conserva supervised.
export const DEFAULT_MODE_BY_LAYOUT = { chain: 'autonomous', parallel: 'supervised' }

export function defaultFacetsByRole() {
  return Object.fromEntries(CHAIN_ROLES.map(r => [r.id, r.defaultFacet]))
}

// capabilities: {capability_key: [motor_key, ...]} de /api/motors/capabilities.
// Un facet de motor solo aparece si capability_motor lo permite para ESA
// capability; los HTTP siempre (su admisión no depende de un binding).
export function facetOptionsFor(role, capabilities) {
  const allowedMotors = capabilities[role.capability] || []
  return [...HTTP_FACETS, ...GOVERNED_FACETS.filter(f => allowedMotors.includes(f))]
}

// Sin timeout_seconds a propósito: el techo por capability lo pone la DB
// (capability.max_execution_minutes) -- ver el incidente b8f80733.
export function buildChainSteps(objective, facetsByRole, instructions) {
  return CHAIN_ROLES.map(role => {
    const facet = facetsByRole[role.id]
    const step = {
      facet,
      capability: role.capability,
      prompt: `${instructions[role.id]}\n\n---\n${objective}`,
      depends_on: role.dependsOn,
      skip_on_fail: false,
    }
    // T5: el motor que dice el selector, no el que resuelva la política de
    // competencia por prioridad global.
    if (GOVERNED_FACETS.includes(facet)) step.motor = facet
    return step
  })
}

// Espejo de jacobs/plan.py::_check_cleanroom: quien produce no aprueba. El
// servidor rechaza igual (422); esto avisa ANTES de enviar. Sobre pasos
// arbitrarios (continuar, spec 2026-09-17 §6.2): índices = posición.
export function cleanroomViolationsDePasos(pasos) {
  const violations = []
  pasos.forEach((paso, i) => {
    if (!AUDIT_CAPABILITIES.has(paso.capability)) return
    for (const dep of paso.depends_on || []) {
      if (pasos[dep] && pasos[dep].facet === paso.facet) {
        violations.push({ paso: i, facet: paso.facet, dependsOn: dep })
      }
    }
  })
  return violations
}

export function cleanroomViolations(facetsByRole) {
  const pasos = CHAIN_ROLES.map(role => ({
    facet: facetsByRole[role.id], capability: role.capability, depends_on: role.dependsOn,
  }))
  return cleanroomViolationsDePasos(pasos).map(v => ({
    role: CHAIN_ROLES[v.paso].id, facet: v.facet, dependsOnRole: CHAIN_ROLES[v.dependsOn].id,
  }))
}
