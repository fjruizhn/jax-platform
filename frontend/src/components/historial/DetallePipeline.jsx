import { useEffect, useState } from 'react'
import api from '../../api/client'
import { useI18n } from '../../i18n/index.jsx'
import { colorToken, tokenDeFaceta } from '../../tema/tokens'
import AlertaError from '../AlertaError'

// StepStatus (jax/jacobs/models.py), distinto del status del pipeline
// entero -- mismo criterio que textoDeStatus en RightPanel.jsx: Object.hasOwn
// y, si el valor no está en el diccionario, un texto genérico, nunca crudo.
function textoDeStepStatus(t, status) {
  return typeof status === 'string' && Object.hasOwn(t.stepStatusLabels, status)
    ? t.stepStatusLabels[status] : t.stepStatusDesconocido
}

// El resultado de un paso puede tener 20.000 tokens (brief de la Task 9). Un
// <pre> sin tope de alto haría que UN paso empujara el resto de la pantalla
// fuera de vista; <details> arranca cerrado (nadie lee 20k tokens de arranque)
// y, ya abierto, max-h-80 + overflow-y-auto le da su propio scroll interno en
// vez de estirar la página entera.
function BloqueLargo({ resumen, children }) {
  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-xs font-semibold text-texto-suave select-none">{resumen}</summary>
      <pre className="mt-1 max-h-80 overflow-y-auto whitespace-pre-wrap break-words rounded bg-superficie p-2 text-xs text-texto">
        {children}
      </pre>
    </details>
  )
}

function Paso({ step, t }) {
  const modelo = step.modelo_real || t.detalleStepModelUnknown
  const prompt = step.prompt || t.detalleStepPromptEmpty
  const resultado = step.result || t.detalleStepResultEmpty
  const duracion = typeof step.duration_seconds === 'number'
    ? t.detalleStepDuration(step.duration_seconds) : t.detalleStepDurationUnknown
  const dependeTexto = Array.isArray(step.depends_on) && step.depends_on.length > 0
    ? t.detalleStepDependsOn(step.depends_on.map((i) => t.detalleStepNumber(i + 1)).join(', '))
    : t.detalleStepDependsOnNone

  return (
    <li className="rounded-lg border border-borde bg-hundido p-3">
      <div className="flex flex-wrap items-center gap-2 mb-1.5">
        <span className="text-xs font-semibold text-texto-tenue">{t.detalleStepNumber(step.step_index + 1)}</span>
        <span
          className="text-xs font-semibold px-2 py-0.5 rounded border bg-superficie"
          style={{ color: colorToken(tokenDeFaceta(step.facet)), borderColor: colorToken(tokenDeFaceta(step.facet), 0.4) }}
        >
          {step.facet}
        </span>
        <span className="text-xs text-texto-suave">{step.capability}</span>
        <span className="text-xs text-texto-tenue ml-auto">{textoDeStepStatus(t, step.status)}</span>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-texto-suave mb-1.5">
        <span>{t.detalleStepModel}: <span className="font-mono text-texto">{modelo}</span></span>
        <span className="text-texto-tenue">{duracion}</span>
      </div>

      <p className="text-xs text-texto-suave mb-1.5">{dependeTexto}</p>

      <BloqueLargo resumen={t.detalleStepPrompt}>{prompt}</BloqueLargo>

      {step.result_unavailable ? (
        <AlertaError className="mt-2 text-xs">{t.detalleStepResultUnavailable(step.error)}</AlertaError>
      ) : (
        <>
          <BloqueLargo resumen={t.detalleStepResult}>{resultado}</BloqueLargo>
          {step.error && <AlertaError className="mt-2 text-xs">{step.error}</AlertaError>}
        </>
      )}

      {Array.isArray(step.sources) && step.sources.length > 0 && (
        <div className="mt-2 text-xs">
          <span className="text-texto-tenue">{t.pipelineSources}: </span>
          {step.sources.map((s, i) => (
            <a
              key={i}
              href={s.url}
              target="_blank"
              rel="noreferrer"
              className="text-acento-texto hover:underline mr-2"
            >
              {s.title || s.url}
            </a>
          ))}
        </div>
      )}
    </li>
  )
}

// Detalle de UN pipeline (Task 9): faceta, MODELO REAL, PROMPT EXACTO, salida
// completa, duración y de qué pasos dependía, por paso -- lo que hoy sólo se
// ve una vez, volcado al chat, antes de irse hacia arriba para siempre.
export default function DetallePipeline({ pipelineId, nombre, onClose }) {
  const { t } = useI18n()
  const [estado, setEstado] = useState({ data: null, cargando: true, error: false })

  useEffect(() => {
    let vigente = true
    setEstado({ data: null, cargando: true, error: false })
    api.get(`/pipelines/${pipelineId}/results`)
      .then(({ data }) => { if (vigente) setEstado({ data, cargando: false, error: false }) })
      .catch((err) => {
        console.error('DetallePipeline fetch failed', err)
        if (vigente) setEstado({ data: null, cargando: false, error: true })
      })
    return () => { vigente = false }
  }, [pipelineId])

  const { data, cargando, error } = estado
  const titulo = t.detalleTitle(nombre || data?.name || pipelineId)
  const pasos = Array.isArray(data?.steps) ? [...data.steps].sort((a, b) => a.step_index - b.step_index) : []
  const duracionTotal = typeof data?.total_duration_seconds === 'number'
    ? t.detalleTotalDuration(data.total_duration_seconds) : t.detalleTotalDurationUnknown

  return (
    <div className="rounded-lg border border-borde bg-fondo p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-bold text-texto-fuerte">{titulo}</h2>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label={t.historialCloseDetail}
            title={t.historialCloseDetail}
            className="text-xs text-texto-tenue hover:text-texto transition-colors"
          >
            ✕
          </button>
        )}
      </div>

      {cargando && <p className="text-xs text-texto-tenue">{t.detalleLoading}</p>}
      {error && <AlertaError className="text-xs">{t.detalleError}</AlertaError>}

      {!cargando && !error && data && (
        <>
          <p className="text-xs text-texto-tenue mb-3">{duracionTotal}</p>
          <ul className="space-y-3">
            {pasos.map((step) => <Paso key={step.step_index} step={step} t={t} />)}
          </ul>
        </>
      )}
    </div>
  )
}
