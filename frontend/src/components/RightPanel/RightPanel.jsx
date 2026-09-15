import { memo, useState } from 'react'
import { useJaxStore } from '../../store/useJaxStore'
import { useI18n } from '../../i18n/index.jsx'
import StepCard from './StepCard'
import AuditLog from './AuditLog'
import api from '../../api/client'
import AlertaError from '../AlertaError'

function ProgressBar({ steps, t }) {
  if (!steps || steps.length === 0) return null
  const done = steps.filter(s => s.status === 'completed').length
  const pct = Math.round((done / steps.length) * 100)
  return (
    <div className="mt-2 mb-1">
      <div className="flex justify-between text-xs text-texto-tenue mb-1">
        <span>{done}/{steps.length} {t.stepsLabel}</span>
        <span>{pct}%</span>
      </div>
      <div className="h-1 bg-borde rounded-full overflow-hidden">
        <div
          className="h-full rounded-full bg-accion transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

function RightPanel() {
  const activePipelines = useJaxStore((s) => s.activePipelines)
  const { t } = useI18n()
  const [tab, setTab] = useState('pipelines')
  const [cancelling, setCancelling] = useState(false)
  // Último fallo de Aprobar/Cancelar (2026-09-14): antes solo iba a
  // console.error y en la interfaz no pasaba nada. Guarda la clave de i18n
  // (no el texto, para que un cambio de idioma lo traduzca) y el pipeline al
  // que pertenece: sobre otro pipeline no significa nada.
  const [aviso, setAviso] = useState(null)

  const pipelines = Object.values(activePipelines)
  const activePipeline = pipelines.find(p => ['running', 'waiting_gate'].includes(p.status))
    || pipelines[0]

  async function handleResume(pipelineId) {
    setAviso(null)
    try {
      await api.post(`/pipelines/${pipelineId}/resume`)
    } catch (e) {
      console.error('resume failed', e)
      setAviso({ pipelineId, clave: 'approveError' })
    }
  }

  async function handleCancel(pipelineId) {
    setAviso(null)
    setCancelling(true)
    try {
      await api.post(`/pipelines/${pipelineId}/cancel`)
    } catch (e) {
      console.error('cancel failed', e)
      setAviso({ pipelineId, clave: 'cancelError' })
    } finally {
      setCancelling(false)
    }
  }

  // El aviso de Aprobar deja de aplicar si el pipeline ya no espera aprobación.
  const avisoVigente = aviso
    && activePipeline
    && aviso.pipelineId === activePipeline.pipeline_id
    && (aviso.clave !== 'approveError' || activePipeline.status === 'waiting_gate')
    ? aviso : null

  const TABS = [
    { id: 'pipelines', label: t.tabDirectorJacobs },
    { id: 'log',       label: t.tabAudit },
  ]

  return (
    <div className="flex flex-col h-full bg-fondo border-l border-borde">
      {/* Tabs */}
      <div className="flex border-b border-borde">
        {TABS.map(({ id: tabId, label }) => (
          <button
            key={tabId}
            onClick={() => setTab(tabId)}
            className={`flex-1 py-2 text-xs uppercase tracking-wider font-semibold transition-colors ${
              tab === tabId
                ? 'text-info border-b-2 border-info'
                : 'text-texto-tenue hover:text-texto'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'pipelines' ? (
        <div className="flex-1 overflow-y-auto">
          {!activePipeline ? (
            <div className="px-4 py-6 text-sm text-texto-tenue text-center">
              {t.noPipelinesActive}
            </div>
          ) : (
            <div className="p-3">
              <div className="mb-1">
                <div className="text-xs font-semibold text-texto truncate">
                  {activePipeline.name}
                </div>
                <div className="text-xs text-texto-tenue font-mono">
                  {activePipeline.pipeline_id?.slice(0, 12)}…
                </div>
                <div className={`text-xs mt-0.5 font-semibold ${
                  activePipeline.status === 'waiting_gate'
                    ? 'text-aviso'
                    : activePipeline.status === 'running'
                    ? 'text-info'
                    : 'text-texto-suave'
                }`}>
                  {t.pipelineStatusLabels[activePipeline.status] || activePipeline.status}
                </div>
              </div>

              <ProgressBar steps={activePipeline.steps} t={t} />

              <div className="mt-2">
                {(activePipeline.steps || []).map((step) => (
                  <StepCard key={step.step_id} step={step} pipelineId={activePipeline.pipeline_id} />
                ))}
              </div>

              {activePipeline.status === 'waiting_gate' && (
                <button
                  onClick={() => handleResume(activePipeline.pipeline_id)}
                  className="mt-3 w-full py-2 rounded-lg bg-accion hover:bg-accion-hover text-sobre-color text-xs font-bold uppercase tracking-widest transition-colors"
                >
                  {t.approve}
                </button>
              )}

              <button
                onClick={() => handleCancel(activePipeline.pipeline_id)}
                disabled={cancelling}
                className="mt-2 w-full py-1.5 rounded-lg bg-superficie hover:bg-peligro-fondo border border-borde hover:border-peligro-borde text-texto-tenue hover:text-peligro text-xs font-semibold transition-colors disabled:opacity-40"
              >
                {cancelling ? t.cancelling : t.cancelPipeline}
              </button>

              {avisoVigente && (
                <AlertaError className="mt-2 text-xs">
                  {t[avisoVigente.clave] ?? t.statusError}
                </AlertaError>
              )}

              {pipelines.length > 1 && (
                <div className="mt-3 text-xs text-texto-tenue">
                  {t.pipelinesAdditional(pipelines.length - 1)}
                </div>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="flex-1 overflow-hidden">
          <AuditLog />
        </div>
      )}
    </div>
  )
}

export default memo(RightPanel)
