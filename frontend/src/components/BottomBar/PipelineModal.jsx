import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'
import api from '../../api/client'

// capability/desc son de otro sistema (las_manos, tablas motor/capability/
// capability_motor -- R4) -- label/color vienen de facetsState (/api/facets).
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
    color: facetsState[f.id]?.color || '#94a3b8',
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
// desactualizado sin que nada lo avise.
const GOVERNED_FACETS = ['jax_local', 'kimi']

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
        timeout_seconds: 300,
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

  const [mode, setMode] = useState('supervised')
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

  async function handleSubmit() {
    if (selected.length === 0 || !catalogReady) return
    setSubmitting(true)
    const steps = buildSteps(selected, objective, FACET_OPTIONS, motorChoices, motorsByKey)
    await onSubmit({
      name: `Pipeline: ${objective.slice(0, 50)}`,
      objective,
      invoked_by: 'Fernando',
      mode,
      max_steps: Math.max(steps.length, 1),
      steps: steps.length > 0 ? steps : null,
    })
    setSubmitting(false)
    onClose()
  }

  const PIPELINE_MODES = [
    { id: 'supervised',  label: '👁 Supervised' },
    { id: 'autonomous',  label: '⚡ Autonomous' },
    { id: 'dry_run',     label: '🧪 Dry run' },
  ]

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.7)' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-slate-900 border border-slate-700 rounded-xl p-5 w-full max-w-md shadow-2xl">
        <div className="mb-4">
          <h2 className="text-sm font-bold text-slate-200 uppercase tracking-widest">
            {t.newPipelineTitle}
          </h2>
          <p className="text-xs text-slate-500 mt-1 truncate">
            {t.objectiveLabel}: {objective}
          </p>
        </div>

        {/* Modo */}
        <div className="mb-4">
          <p className="text-xs font-semibold text-slate-400 mb-2 uppercase tracking-wider">{t.modeLabel}</p>
          <div className="flex gap-2">
            {PIPELINE_MODES.map(({ id: m, label }) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={`flex-1 py-1.5 rounded-lg text-xs font-semibold border transition-colors ${
                  mode === m
                    ? m === 'autonomous'
                      ? 'border-orange-500 bg-orange-500/20 text-orange-300'
                      : 'border-blue-500 bg-blue-500/20 text-blue-300'
                    : 'border-slate-700 bg-slate-800 text-slate-500 hover:text-slate-300'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* Facetas */}
        <div className="mb-5">
          <p className="text-xs font-semibold text-slate-400 mb-2 uppercase tracking-wider">
            {t.facetsLabel}
          </p>
          <div className="space-y-1.5">
            {FACET_OPTIONS.map(f => {
              const cap = motorsByKey ? _capabilityFor(f.id, motorsByKey) : null
              const motorOptions = cap ? (capabilities[cap] || []) : []
              return (
                <div key={f.id}>
                  <label
                    className={`flex items-center gap-3 p-2 rounded-lg border cursor-pointer transition-colors ${
                      selected.includes(f.id)
                        ? 'border-opacity-60 bg-opacity-10'
                        : 'border-slate-800 bg-slate-800/50 hover:border-slate-700'
                    }`}
                    style={selected.includes(f.id) ? {
                      borderColor: f.color + '80',
                      backgroundColor: f.color + '12',
                    } : {}}
                  >
                    <input
                      type="checkbox"
                      checked={selected.includes(f.id)}
                      onChange={() => toggleFacet(f.id)}
                      className="sr-only"
                    />
                    <span
                      className="w-4 h-4 rounded border-2 flex items-center justify-center flex-shrink-0 text-xs"
                      style={selected.includes(f.id) ? {
                        borderColor: f.color,
                        backgroundColor: f.color,
                        color: '#000',
                      } : { borderColor: '#475569' }}
                    >
                      {selected.includes(f.id) ? '✓' : ''}
                    </span>
                    <span className="text-xs font-semibold" style={{ color: f.color }}>{f.label}</span>
                    <span className="text-xs text-slate-500">{f.desc}</span>
                  </label>
                  {selected.includes(f.id) && motorOptions.length > 0 && (
                    // T5: default = f.id (el motor que el checkbox dice),
                    // no '' (auto) -- '' sigue disponible como elección
                    // EXPLÍCITA del usuario, ya no como default silencioso.
                    <select
                      className="ml-7 mt-1 text-xs bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-300"
                      value={motorChoices[f.id] !== undefined ? motorChoices[f.id] : f.id}
                      onChange={(e) => setMotorFor(f.id, e.target.value)}
                    >
                      <option value="">{t.autoMotor}</option>
                      {motorOptions.map(m => <option key={m} value={m}>{m}</option>)}
                    </select>
                  )}
                  {selected.includes(f.id) && motorsByKey && !cap && (
                    <p className="ml-7 mt-1 text-[11px] text-amber-500/80">
                      {t.facetUngoverned}
                    </p>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        {/* T5: fail-closed -- sin catálogo real (cargando o falló), no se
            arma ningún plan. Nada de fallback silencioso. */}
        {catalogFailed && (
          <p className="mb-2 text-[11px] text-red-400">{t.catalogFailedHint}</p>
        )}
        {!catalogFailed && !catalogReady && (
          <p className="mb-2 text-[11px] text-slate-500">{t.catalogLoadingHint}</p>
        )}

        {/* Botones */}
        <div className="flex gap-2">
          <button
            onClick={onClose}
            className="flex-1 py-2 rounded-lg text-xs font-semibold bg-slate-800 text-slate-400 hover:text-slate-200 border border-slate-700 transition-colors"
          >
            {t.cancel}
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting || selected.length === 0 || !catalogReady}
            className="flex-1 py-2 rounded-lg text-xs font-bold text-white transition-colors disabled:opacity-40"
            style={{ backgroundColor: '#3b82f6' }}
          >
            {submitting ? t.starting : t.planAndExecute}
          </button>
        </div>
      </div>
    </div>
  )
}
