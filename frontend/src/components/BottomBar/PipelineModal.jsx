import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import api from '../../api/client'
import { colorToken } from '../../tema/tokens'
import {
  GOVERNED_FACETS,
  CHAIN_ROLES,
  facetOptionsFor,
  buildChainSteps,
  cleanroomViolations,
  defaultFacetsByRole,
  DEFAULT_MODE_BY_LAYOUT,
} from './pipelineChain'

// capability/desc son de otro sistema (las_manos, tablas motor/capability/
// capability_motor -- R4) -- label y token vienen de facetsState (/api/facets).
function getFacetOptions(t, facetsState) {
  return [
    { id: 'jax_local', capability: 'reasoning',       desc: t.descJaxLocal },
    { id: 'hipatia',   capability: 'research',         desc: t.descHipatia },
    { id: 'jekyll',    capability: 'analysis',         desc: t.descJekyll },
    { id: 'thot',      capability: 'critique',         desc: t.descThot },
    { id: 'kimi',      capability: 'implementation',   desc: t.descKimi },
    { id: 'ada',       capability: 'analysis',         desc: t.descAda },
  ].map(f => ({
    ...f,
    label: facetsState[f.id]?.display_name || facetsState[f.id]?.name || f.id,
    token: facetsState[f.id]?.token || 'texto-suave',
  }))
}

// T5 (2026-08-22, diagnóstico pipeline 19ad2c42-cdf): antes esto era un mapa
// hardcodeado (GOVERNED_FACET_CAPABILITY) que decidía capability sin mirar
// el catálogo real -- exactamente la causa raíz del incidente.
//
// "Gobernado" NO es "tiene fila en `motorsByKey`" -- ada/thot SÍ tienen fila
// en `motor` (transport http_openai_compat, has_tool_access=false) pero
// despachan por HTTP directo (jacobs/executor.py::_HTTP_FACETS), fuera del
// alcance de T2 (_validate_plan_capabilities solo cubre jacobs.models.
// MOTOR_FACETS). Confundir las dos cosas fue un bug real de esta misma
// ronda -- encontrado por un test que esperaba 1 <select> y encontró 2
// (kimi Y thot, porque thot SÍ tiene fila en `motor`). GOVERNED_FACETS
// replica MOTOR_FACETS del backend (jax/jacobs/models.py) a mano -- no hay
// endpoint que exponga la partición HTTP-directo/Motor-Registry todavía
// (viven en repos distintos, jax vs jax-platform). Deuda declarada, no
// resuelta: si MOTOR_FACETS cambia en jacobs/models.py, este array queda
// desactualizado sin que nada lo avise. (Vive en pipelineChain.js desde
// 2026-09-12: una sola copia, compartida con la cadena.)

// La capability que se pide para un facet gobernado depende de
// has_tool_access (dato real, T1), no de una tabla fija: 'implementation'
// (output_schema=code_patch.v1) es un callejón sin salida en este picker --
// no hay forma de armar un step reconcile/assemble downstream que aplique
// el patch (buildSteps no tiene noción de depends_on). 'file_write' es
// autocontenida (el motor ejecuta la tool dentro del job, sin consumidor)
// -- se pide SOLO si el motor puede ejecutarla; si no, 'generate' (texto
// libre, nunca promete escribir nada que el motor no puede).
function _capabilityFor(facetId, motorsByKey) {
  if (!GOVERNED_FACETS.includes(facetId)) return null  // no gobernado por T2
  const entry = motorsByKey[facetId]
  if (!entry) return null  // el catálogo no trajo fila para este motor -- no arriesgar
  return entry.has_tool_access ? 'file_write' : 'generate'
}

function buildSteps(selectedFacets, objective, facetOptions, motorChoices, motorsByKey) {
  return facetOptions
    .filter(f => selectedFacets.includes(f.id))
    .map(f => {
      const governedCapability = _capabilityFor(f.id, motorsByKey)
      const step = {
        facet: f.id,
        capability: governedCapability || f.capability,
        prompt: `${f.desc}: ${objective}`,
        // Sin timeout_seconds a propósito: el techo por capability lo pone
        // la DB (capability.max_execution_minutes). Un 300 fijo acá lo
        // pisaba y abortó el pipeline b8f80733 (2026-09-12).
        skip_on_fail: false,
      }
      if (governedCapability) {
        // T5: motor SIEMPRE fijado para facets gobernados, salvo que el
        // usuario elija explícitamente "Auto" en el <select> (motorChoices
        // guarda '' en ese caso -- una elección real, no una ausencia).
        // Antes quedaba sin setear por default y MotorPolicy._resolve_motor
        // (None, cap) resolvía por prioridad GLOBAL de capability_motor,
        // ignorando el facet -- confirmado en vivo: un step etiquetado
        // "jax_local" se ejecutó contra kimi. El checkbox debe garantizar
        // el motor que dice.
        const choice = motorChoices[f.id]
        if (choice === undefined) {
          step.motor = f.id
        } else if (choice !== '') {
          step.motor = choice
        }
      }
      return step
    })
}

export default function PipelineModal({ objective, onClose, onSubmit }) {
  const { t } = useI18n()
  const facetsState = useJaxStore((s) => s.facets)
  const FACET_OPTIONS = getFacetOptions(t, facetsState)

  // Cadena por defecto: es el uso que pidió Fernando (2026-09-12). Paralelo
  // sigue a un clic. El modo sigue a la forma hasta que el usuario elige uno.
  const [layout, setLayout] = useState('chain')
  const [mode, setMode] = useState(DEFAULT_MODE_BY_LAYOUT.chain)
  const [modeTouched, setModeTouched] = useState(false)
  const [chainFacets, setChainFacets] = useState(defaultFacetsByRole)
  const [selected, setSelected] = useState(['hipatia', 'jekyll', 'thot'])
  const [submitting, setSubmitting] = useState(false)
  const [capabilities, setCapabilities] = useState({})  // {capability_key: [motor_key, ...]}
  // T5: null = catálogo todavía no resolvió (fail-closed mientras carga);
  // {} tras un fetch exitoso (aunque vacío) es un estado válido, distinto
  // de "no cargó todavía" -- por eso null, no {}, como valor inicial.
  const [motorsByKey, setMotorsByKey] = useState(null)  // {motor_key: {has_tool_access}}
  const [catalogFailed, setCatalogFailed] = useState(false)
  const [motorChoices, setMotorChoices] = useState({})  // {facet_id: motor_key | ''}

  useEffect(() => {
    // api.get (no fetch crudo) -- el interceptor de src/api/client.js inyecta
    // Authorization: Bearer <token> desde el store; el JWT vive solo en
    // memoria (nunca en cookie), asi que fetch() con credentials:'include'
    // nunca autentica esta llamada.
    api.get('/motors/capabilities')
      .then(({ data }) => {
        const byCap = {}
        for (const c of data.capabilities) byCap[c.key] = c.allowed_motors
        setCapabilities(byCap)
        const byMotor = {}
        for (const m of data.motors || []) byMotor[m.key] = m
        setMotorsByKey(byMotor)
      })
      // T5: fail-closed -- si el catálogo falla, motorsByKey queda null
      // para siempre (catalogReady abajo nunca se pone true). No hay
      // fallback a un mapa hardcodeado: eso es exactamente el bug que
      // causó el incidente (pedir el dato real y decidir con otra cosa).
      .catch(() => setCatalogFailed(true))
  }, [])

  // catalogReady: false mientras carga (motorsByKey===null) Y false si
  // falló -- las dos son la misma señal para el usuario ("no arranques
  // todavía"), aunque la causa sea distinta.
  const catalogReady = motorsByKey !== null && !catalogFailed

  function toggleFacet(id) {
    setSelected(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id])
  }

  function setMotorFor(facetId, motorKey) {
    setMotorChoices(m => ({ ...m, [facetId]: motorKey }))
  }

  const facetLabel = (id) => FACET_OPTIONS.find(f => f.id === id)?.label || id
  const violations = cleanroomViolations(chainFacets)
  // Solo con el catálogo cargado: antes, las opciones de motor no existen y
  // todo parecería inválido.
  const invalidRoles = catalogReady
    ? CHAIN_ROLES.filter(r => !facetOptionsFor(r, capabilities).includes(chainFacets[r.id]))
    : []
  const chainBlocked = violations.length > 0 || invalidRoles.length > 0

  async function handleSubmit() {
    if (!catalogReady) return
    if (layout === 'chain') {
      if (chainBlocked) return
      setSubmitting(true)
      const steps = buildChainSteps(objective, chainFacets, t.chainInstructions)
      await onSubmit({
        name: t.pipelineName(objective),
        objective,
        mode,
        max_steps: steps.length,
        steps,
      })
      setSubmitting(false)
      onClose()
      return
    }
    if (selected.length === 0) return
    setSubmitting(true)
    const steps = buildSteps(selected, objective, FACET_OPTIONS, motorChoices, motorsByKey)
    await onSubmit({
      name: t.pipelineName(objective),
      objective,
      mode,
      max_steps: Math.max(steps.length, 1),
      steps: steps.length > 0 ? steps : null,
    })
    setSubmitting(false)
    onClose()
  }

  const PIPELINE_MODES = [
    { id: 'supervised',  label: t.pipelineModeSupervised },
    { id: 'autonomous',  label: t.pipelineModeAutonomous },
    { id: 'dry_run',     label: t.pipelineModeDryRun },
  ]

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-fondo/70"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-superficie border border-borde rounded-xl p-5 w-full max-w-md shadow-2xl">
        <div className="mb-4">
          <h2 className="text-sm font-bold text-texto uppercase tracking-widest">
            {t.newPipelineTitle}
          </h2>
          <p className="text-xs text-texto-tenue mt-1 truncate">
            {t.objectiveLabel}: {objective}
          </p>
        </div>

        {/* Modo */}
        <div className="mb-4">
          <p className="text-xs font-semibold text-texto-suave mb-2 uppercase tracking-wider">{t.modeLabel}</p>
          <div className="flex gap-2">
            {PIPELINE_MODES.map(({ id: m, label }) => (
              <button
                key={m}
                onClick={() => { setMode(m); setModeTouched(true) }}
                className={`flex-1 py-1.5 rounded-lg text-xs font-semibold border transition-colors ${
                  mode === m
                    ? m === 'autonomous'
                      ? 'border-aviso-borde bg-aviso-fondo text-aviso'
                      : 'border-info bg-info-fondo text-info'
                    : 'border-borde bg-hundido text-texto-tenue hover:text-texto'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* Forma: en cadena (depends_on) o en paralelo (lista plana) */}
        <div className="mb-4">
          <p className="text-xs font-semibold text-texto-suave mb-2 uppercase tracking-wider">{t.layoutLabel}</p>
          <div className="flex gap-2">
            {[['chain', t.layoutChain], ['parallel', t.layoutParallel]].map(([id, label]) => (
              <button
                key={id}
                onClick={() => {
                  setLayout(id)
                  if (!modeTouched) setMode(DEFAULT_MODE_BY_LAYOUT[id])
                }}
                aria-pressed={layout === id}
                className={`flex-1 py-1.5 rounded-lg text-xs font-semibold border transition-colors ${
                  layout === id
                    ? 'border-info bg-info-fondo text-info'
                    : 'border-borde bg-hundido text-texto-tenue hover:text-texto'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {layout === 'chain' && (
          <div className="mb-5">
            <p className="text-xs font-semibold text-texto-suave mb-1 uppercase tracking-wider">{t.chainLabel}</p>
            <p className="text-[11px] text-texto-tenue mb-2">{t.chainHint}</p>
            <ol className="space-y-1.5">
              {CHAIN_ROLES.map((role, i) => (
                <li
                  key={role.id}
                  className="flex items-center gap-2 p-2 rounded-lg border border-borde bg-hundido"
                >
                  <span className="text-xs font-semibold text-texto-tenue w-4">{i + 1}.</span>
                  <span className="text-xs text-texto flex-1">{t.chainRoles[role.id]}</span>
                  <select
                    aria-label={t.chainRoles[role.id]}
                    className="text-xs bg-hundido border border-borde-control rounded px-2 py-1 text-texto focus:outline-none focus:border-foco"
                    value={chainFacets[role.id]}
                    onChange={(e) => setChainFacets(f => ({ ...f, [role.id]: e.target.value }))}
                  >
                    {facetOptionsFor(role, capabilities).map(fid => (
                      <option key={fid} value={fid}>{facetLabel(fid)}</option>
                    ))}
                  </select>
                </li>
              ))}
            </ol>
            {violations.map(v => (
              <p key={`${v.role}-${v.dependsOnRole}`} className="mt-2 text-[11px] text-peligro">
                {t.chainCleanroomWarning(t.chainRoles[v.role], facetLabel(v.facet), t.chainRoles[v.dependsOnRole])}
              </p>
            ))}
            {invalidRoles.map(r => (
              <p key={`invalid-${r.id}`} className="mt-2 text-[11px] text-peligro">
                {t.chainInvalidFacet(t.chainRoles[r.id])}
              </p>
            ))}
          </div>
        )}

        {/* Facetas (en paralelo) */}
        {layout === 'parallel' && (
        <div className="mb-5">
          <p className="text-xs font-semibold text-texto-suave mb-2 uppercase tracking-wider">
            {t.facetsLabel}
          </p>
          <div className="space-y-1.5">
            {FACET_OPTIONS.map(f => {
              const cap = motorsByKey ? _capabilityFor(f.id, motorsByKey) : null
              const motorOptions = cap ? (capabilities[cap] || []) : []
              return (
                <div key={f.id}>
                  <label
                    className={`flex items-center gap-3 p-2 rounded-lg border border-borde bg-hundido cursor-pointer transition-colors ${
                      selected.includes(f.id) ? '' : 'hover:border-borde-control'
                    }`}
                    style={selected.includes(f.id) ? { borderColor: colorToken(f.token, 0.5) } : {}}
                  >
                    <input
                      type="checkbox"
                      checked={selected.includes(f.id)}
                      onChange={() => toggleFacet(f.id)}
                      className="sr-only"
                    />
                    {/* Tilde (Ruling 30): elegida = superficie con borde y ✓
                        del color de la faceta; nunca texto sobre el color sólido. */}
                    <span
                      className={`w-4 h-4 rounded border-2 flex items-center justify-center flex-shrink-0 text-xs ${
                        selected.includes(f.id) ? 'bg-superficie' : 'border-borde-control'
                      }`}
                      style={selected.includes(f.id) ? {
                        borderColor: colorToken(f.token),
                        color: colorToken(f.token),
                      } : undefined}
                    >
                      {selected.includes(f.id) ? '✓' : ''}
                    </span>
                    <span className="text-xs font-semibold" style={{ color: colorToken(f.token) }}>{f.label}</span>
                    <span className="text-xs text-texto-tenue">{f.desc}</span>
                  </label>
                  {selected.includes(f.id) && motorOptions.length > 0 && (
                    // T5: default = f.id (el motor que el checkbox dice),
                    // no '' (auto) -- '' sigue disponible como elección
                    // EXPLÍCITA del usuario, ya no como default silencioso.
                    <select
                      className="ml-7 mt-1 text-xs bg-hundido border border-borde-control rounded px-2 py-1 text-texto focus:outline-none focus:border-foco"
                      value={motorChoices[f.id] !== undefined ? motorChoices[f.id] : f.id}
                      onChange={(e) => setMotorFor(f.id, e.target.value)}
                    >
                      <option value="">{t.autoMotor}</option>
                      {motorOptions.map(m => <option key={m} value={m}>{m}</option>)}
                    </select>
                  )}
                  {selected.includes(f.id) && motorsByKey && !cap && (
                    <p className="ml-7 mt-1 text-[11px] text-aviso">
                      {t.facetUngoverned}
                    </p>
                  )}
                </div>
              )
            })}
          </div>
        </div>
        )}

        {/* T5: fail-closed -- sin catálogo real (cargando o falló), no se
            arma ningún plan. Nada de fallback silencioso. */}
        {catalogFailed && (
          <p className="mb-2 text-[11px] text-peligro">{t.catalogFailedHint}</p>
        )}
        {!catalogFailed && !catalogReady && (
          <p className="mb-2 text-[11px] text-texto-tenue">{t.catalogLoadingHint}</p>
        )}

        {/* Botones */}
        <div className="flex gap-2">
          <button
            onClick={onClose}
            className="flex-1 py-2 rounded-lg text-xs font-semibold bg-hundido text-texto-suave hover:text-texto border border-borde transition-colors"
          >
            {t.cancel}
          </button>
          <button
            onClick={handleSubmit}
            disabled={
              submitting || !catalogReady
              || (layout === 'chain' ? chainBlocked : selected.length === 0)
            }
            className="flex-1 py-2 rounded-lg text-xs font-bold bg-accion hover:bg-accion-hover text-sobre-color transition-colors disabled:opacity-40"
          >
            {submitting ? t.starting : t.planAndExecute}
          </button>
        </div>
      </div>
    </div>
  )
}
