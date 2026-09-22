import { useEffect, useState } from 'react'
import api from '../../api/client'
import { useI18n, localeFor } from '../../i18n/index.jsx'
import { colorToken, tokenDeFaceta } from '../../tema/tokens'
import { TAMANO_MINIMO_TOQUE } from '../../tema/botones'
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

// 2026-09-22 (cierre de los dos huecos de la revisión final de Descartar
// Pipelines, punto 2): PIPELINE_DISCARDED/RECOVERED/HIDDEN/RESTORED en
// palabras -- hasta hoy sólo se leían con `mysql` a mano. `user_id` se
// muestra TAL CUAL viene del backend (payload de jacobs_events): no se
// inventa una búsqueda de nombre (spec de esta tarea, punto 2, último ítem).
const _TEXTO_DE_EVENTO_AUDITORIA = {
  PIPELINE_DISCARDED: 'auditoriaDescartado',
  PIPELINE_RECOVERED: 'auditoriaRecuperado',
  PIPELINE_HIDDEN: 'auditoriaOcultado',
  PIPELINE_RESTORED: 'auditoriaRestaurado',
}

function EventoAuditoria({ evento, t, lang }) {
  const clave = _TEXTO_DE_EVENTO_AUDITORIA[evento.event_type]
  if (!clave) return null // tipo que esta versión no conoce: no se inventa un texto
  const fecha = typeof evento.ts === 'number' ? new Date(evento.ts * 1000).toLocaleString(localeFor(lang)) : '—'
  return <li className="text-xs text-texto-suave">{t[clave](evento.user_id, fecha)}</li>
}

function Paso({ step, t }) {
  const modelo = step.modelo_real || t.detalleStepModelUnknown
  const prompt = step.prompt || t.detalleStepPromptEmpty
  const resultado = step.result || t.detalleStepResultEmpty
  // Menor del revisor (ronda de arreglo 1): sin formatear, un round(...,2/3)
  // del backend podía imprimirse "10.234s" o, al revés, "3s" para un 3.0 --
  // un decimal siempre, mismo criterio que StepCard.jsx.
  const duracion = typeof step.duration_seconds === 'number'
    ? t.detalleStepDuration(step.duration_seconds.toFixed(1)) : t.detalleStepDurationUnknown
  // Menor 4 (revisión final, 2026-09-18): `[]` (paralelo explícito, el plan
  // LO DECIDIÓ) y ausente (paso de antes de que la columna existiera, "no
  // sé") ya no comparten texto -- sólo `[]` afirma el NO.
  const dependeTexto = !Array.isArray(step.depends_on)
    ? t.detalleStepDependsOnUnknown
    : step.depends_on.length > 0
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
        // Ronda de arreglo 2 (2026-09-18): con ~30 fuentes (investigación de
        // mercado real, captura de Fernando) la lista se salía del borde de
        // la tarjeta y seguía hasta fuera de la pantalla. `flex flex-wrap` en
        // el contenedor (antes texto inline con `mr-2`, sin ancho declarado)
        // + `break-all` en cada enlace (una URL sin espacios es un solo
        // token: el wrap normal corta en espacios, no adentro de la palabra)
        // cubre los dos casos que pidió: muchas fuentes cortas Y una sola
        // URL larguísima sin espacios.
        <div className="mt-2 text-xs flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="text-texto-tenue">{t.pipelineSources}:</span>
          {step.sources.map((s, i) => (
            <a
              key={i}
              href={s.url}
              target="_blank"
              rel="noreferrer"
              className="text-acento-texto hover:underline break-all"
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
  const { t, lang } = useI18n()
  const [estado, setEstado] = useState({ data: null, cargando: true, error: false, notFound: false })
  // Auditoría de descarte (punto 2, 2026-09-22): estado APARTE, sin
  // cargando/error propios -- es una sección secundaria del detalle, no un
  // segundo detalle. Si falla o no hay eventos, la sección simplemente no
  // se muestra (spec: "si no hay, no se muestra nada, no una caja vacía");
  // un fallo acá nunca tapa ni compite con el error de /results, que sigue
  // siendo el único que decide cargando/error/notFound de la pantalla.
  const [eventosAuditoria, setEventosAuditoria] = useState([])

  useEffect(() => {
    let vigente = true
    setEstado({ data: null, cargando: true, error: false, notFound: false })
    setEventosAuditoria([])
    api.get(`/pipelines/${pipelineId}/results`)
      .then(({ data }) => { if (vigente) setEstado({ data, cargando: false, error: false, notFound: false }) })
      .catch((err) => {
        // Ronda de arreglo 1 (2026-09-18): _require_pipeline_owner
        // (jax-platform/backend/api/pipelines.py) devuelve 404 A PROPÓSITO
        // tanto si el pipeline_id no existe como si es de otro dueño -- para
        // no confirmarle a quien pregunta cuál de los dos es. La pantalla
        // respeta esa ambigüedad: un estado vacío propio, no el error
        // genérico (que sugiere reintentar -- un 404 no se arregla así) ni
        // ningún detalle de por qué.
        const esNotFound = err?.response?.status === 404
        if (!esNotFound) console.error('DetallePipeline fetch failed', err)
        if (vigente) setEstado({ data: null, cargando: false, error: !esNotFound, notFound: esNotFound })
      })
    api.get(`/pipelines/${pipelineId}/auditoria-descarte`)
      .then(({ data }) => {
        if (vigente) setEventosAuditoria(Array.isArray(data?.eventos) ? data.eventos : [])
      })
      .catch((err) => {
        // Mismo 404 por ownership que /results (misma guardia en el
        // backend) o cualquier otro fallo: la sección no se muestra, sin
        // un segundo cartel de error -- ver el comentario de más arriba.
        if (err?.response?.status !== 404) console.error('DetallePipeline auditoria-descarte fetch failed', err)
        if (vigente) setEventosAuditoria([])
      })
    return () => { vigente = false }
  }, [pipelineId])

  const { data, cargando, error, notFound } = estado
  const titulo = t.detalleTitle(nombre || data?.name || pipelineId)
  const pasos = Array.isArray(data?.steps) ? [...data.steps].sort((a, b) => a.step_index - b.step_index) : []
  const duracionTotal = typeof data?.total_duration_seconds === 'number'
    ? t.detalleTotalDuration(data.total_duration_seconds.toFixed(1)) : t.detalleTotalDurationUnknown

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
            className={`${TAMANO_MINIMO_TOQUE} text-xs text-texto-tenue hover:text-texto transition-colors`}
          >
            ✕
          </button>
        )}
      </div>

      {cargando && <p className="text-xs text-texto-tenue">{t.detalleLoading}</p>}
      {error && <AlertaError className="text-xs">{t.detalleError}</AlertaError>}
      {notFound && <p className="text-xs text-texto-tenue">{t.detalleNotFound}</p>}

      {!cargando && !error && data && (
        <>
          <p className="text-xs text-texto-tenue mb-3">{duracionTotal}</p>
          <ul className="space-y-3">
            {pasos.map((step) => <Paso key={step.step_index} step={step} t={t} />)}
          </ul>

          {eventosAuditoria.length > 0 && (
            <div className="mt-4 pt-3 border-t border-borde">
              <h3 className="text-xs font-semibold text-texto-suave uppercase tracking-wider mb-2">{t.auditoriaDescarteTitulo}</h3>
              <ul className="space-y-1">
                {eventosAuditoria.map((evento, i) => (
                  <EventoAuditoria key={i} evento={evento} t={t} lang={lang} />
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  )
}
