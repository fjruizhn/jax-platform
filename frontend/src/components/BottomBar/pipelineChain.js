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

// Espejo de jacobs/plan.py::PlanBuilder._con_arbitro, regla "sala limpia"
// (Ruling 2/11 del ledger 2026-09-18): el servidor agrega SOLO, al final de
// CUALQUIER plan de 2+ pasos, un step árbitro con esta faceta -- hoy 'thot',
// leída en el servidor de axioma_config.ejecutor.auditor_faceta. Si esa
// faceta YA aparece como productor en el plan (cadena o paralelo, CUALQUIER
// capability, con o sin depends_on), el plan entero se rechaza con 422
// arbitro_no_disponible/sala-limpia -- "quien produce no arbitra", sin
// excepción de largo de plan.
// Deuda declarada, misma clase que GOVERNED_FACETS/HTTP_FACETS arriba: no
// hay endpoint que exponga axioma_config desde este repo (T5, 2026-08-22),
// así que queda hardcodeada acá también. Si se reconfigura el árbitro en el
// servidor sin tocar esta constante, esta pantalla vuelve a mentir -- igual
// que ya mintió una vez (bloqueante de la ronda 2026-09-18: el layout de
// cadena traía 'thot' fijo en el paso "audit" y el paralelo lo traía
// preseleccionado, los dos auto-rechazados por el servidor sin aviso previo
// porque este archivo todavía espejaba la regla VIEJA de cleanroom, que es
// más angosta -- ver cleanroomViolationsDePasos abajo).
export const ARBITRO_FACETA = 'thot'

// dependsOn es el contexto MÍNIMO de cada paso: cada dependencia reenvía su
// salida entera al modelo, y eso se paga en cada llamada (blueprint de
// Ricardo §9/§11).
//
// La crítica entra a "unify" desde 2026-09-12 (decisión de Fernando): sin
// ella la reconciliación medía contra lo que el plan DECLARABA haber
// aceptado, no contra lo que la crítica dijo (E2E b2d87971).
//
// EL PASO "audit" SE SACÓ de la cadena por defecto (fix bloqueante
// 2026-09-18, ronda de arreglo del historial). Historia completa, para que
// nadie lo reponga de buena fe creyendo que fue un olvido:
//
// Esta misma ronda agregó, del lado del servidor (jacobs/plan.py::_con_arbitro),
// un paso árbitro que Jacobs agrega SOLO al final de CUALQUIER plan de 2+
// pasos -- hoy siempre 'thot' (ARBITRO_FACETA arriba) -- que depende de
// TODOS los pasos anteriores y produce una decisión donde cada punto cita
// el paso que lo sostiene. Eso es estrictamente MÁS de lo que "audit" hacía:
// "audit" miraba investigación+crítica+producto (tres de cinco pasos); el
// árbitro los mira TODOS.
// Mantener "audit" habría exigido mutilarlo para esquivar dos reglas a la
// vez: la sala limpia (prohíbe 'thot', que es el único no-productor de la
// cadena) y la auditoría independiente (con 'ada' -- la única otra faceta
// que el catálogo real de capability_motor admite para
// `validate_consistency`, medido contra pipelineChain.test.js::CAPS --
// "audit" no podía seguir dependiendo de "unify", que también es 'ada').
// El resultado hubiera sido un control con la FORMA de "audit" pero que ve
// menos de lo que dice ver -- peor que no tenerlo, porque parece que alguien
// revisó. Mismo razonamiento con el que esta ronda ya sacó el paso fijo de
// validación de consistencia (thot) del prompt modular de Ada del lado del
// servidor: las dos piezas se solapaban en propósito, y la que sobrevive es
// la que ve más.
// Si en el futuro hace falta una revisión INTERMEDIA (no al final, con
// menos contexto que el árbitro) es una necesidad nueva, no la resurrección
// de este paso -- hay que diseñarla de cero contra las reglas de hoy.
export const CHAIN_ROLES = [
  { id: 'research', capability: 'research',             defaultFacet: 'hipatia', dependsOn: [] },
  { id: 'plan',     capability: 'design',               defaultFacet: 'ada',     dependsOn: [0] },
  { id: 'critique', capability: 'critique',             defaultFacet: 'jekyll',  dependsOn: [0, 1] },
  { id: 'unify',    capability: 'reconcile',            defaultFacet: 'ada',     dependsOn: [1, 2] },
  { id: 'produce',  capability: 'generate',             defaultFacet: 'kimi',    dependsOn: [3] },
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

// Espejo de la regla sala-limpia de _con_arbitro (ver ARBITRO_FACETA arriba):
// INCONDICIONAL -- a diferencia de cleanroomViolationsDePasos, no importa la
// capability ni si depende de algo; la sola presencia de la faceta árbitro
// como productor en el plan (cadena, acá por rol) alcanza para el rechazo.
export function arbitroViolations(facetsByRole, arbitroFaceta = ARBITRO_FACETA) {
  return CHAIN_ROLES
    .filter(role => facetsByRole[role.id] === arbitroFaceta)
    .map(role => ({ role: role.id, facet: arbitroFaceta }))
}

// Misma regla, para la selección en paralelo (frontend/src/components/BottomBar/
// PipelineModal.jsx layout='parallel'): ahí cada faceta elegida ES el
// productor directo, sin roles ni depends_on -- alcanza con mirar si la
// faceta árbitro está entre las elegidas.
export function seleccionIncluyeArbitro(seleccionadas, arbitroFaceta = ARBITRO_FACETA) {
  return seleccionadas.includes(arbitroFaceta)
}
