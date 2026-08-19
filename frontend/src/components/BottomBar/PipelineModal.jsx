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

// facet -> capability real de las_manos (las que sí llegan a Motor
// Registry hoy: kimi y jax_local, tras Task 5). El resto de facetas del
// picker (hipatia/jekyll/thot/ada directas) no pasan por acá -- su
// "capability" es solo etiqueta descriptiva, sin motor que elegir.
const GOVERNED_FACET_CAPABILITY = { kimi: 'implementation', jax_local: 'generate' }

function buildSteps(selectedFacets, objective, facetOptions, motorChoices) {
  return facetOptions
    .filter(f => selectedFacets.includes(f.id))
    .map(f => {
      const step = {
        facet: f.id,
        capability: GOVERNED_FACET_CAPABILITY[f.id] || f.capability,
        prompt: `${f.desc}: ${objective}`,
        timeout_seconds: 300,
        skip_on_fail: false,
      }
      if (GOVERNED_FACET_CAPABILITY[f.id] && motorChoices[f.id]) {
        step.motor = motorChoices[f.id]  // vacío/no seteado = None, auto por competencia
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
  const [motorChoices, setMotorChoices] = useState({})  // {facet_id: motor_key | ''}

  useEffect(() => {
    // api.get (no fetch crudo) -- el interceptor de src/api/client.js inyecta
    // Authorization: Bearer <token> desde el store; el JWT vive solo en
    // memoria (nunca en cookie), asi que fetch() con credentials:'include'
    // nunca autentica esta llamada.
    api.get('/motors/capabilities')
      .then(({ data }) => {
        const byKey = {}
        for (const c of data.capabilities) byKey[c.key] = c.allowed_motors
        setCapabilities(byKey)
      })
      .catch(() => setCapabilities({}))
  }, [])

  function toggleFacet(id) {
    setSelected(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id])
  }

  function setMotorFor(facetId, motorKey) {
    setMotorChoices(m => ({ ...m, [facetId]: motorKey }))
  }

  async function handleSubmit() {
    if (selected.length === 0) return
    setSubmitting(true)
    const steps = buildSteps(selected, objective, FACET_OPTIONS, motorChoices)
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
              const cap = GOVERNED_FACET_CAPABILITY[f.id]
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
                    <select
                      className="ml-7 mt-1 text-xs bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-300"
                      value={motorChoices[f.id] || ''}
                      onChange={(e) => setMotorFor(f.id, e.target.value)}
                    >
                      <option value="">{t.autoMotor}</option>
                      {motorOptions.map(m => <option key={m} value={m}>{m}</option>)}
                    </select>
                  )}
                </div>
              )
            })}
          </div>
        </div>

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
            disabled={submitting || selected.length === 0}
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
