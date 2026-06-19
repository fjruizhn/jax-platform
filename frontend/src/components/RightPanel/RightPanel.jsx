import { useState } from 'react'
import { useJaxStore } from '../../store/useJaxStore'
import { useI18n } from '../../i18n/index.jsx'
import StepCard from './StepCard'
import AuditLog from './AuditLog'
import api from '../../api/client'

function ProgressBar({ steps, t }) {
  if (!steps || steps.length === 0) return null
  const done = steps.filter(s => s.status === 'completed').length
  const pct = Math.round((done / steps.length) * 100)
  return (
    <div className="mt-2 mb-1">
      <div className="flex justify-between text-xs text-slate-600 mb-1">
        <span>{done}/{steps.length} {t.stepsLabel}</span>
        <span>{pct}%</span>
      </div>
      <div className="h-1 bg-slate-800 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: '#3b82f6' }}
        />
      </div>
    </div>
  )
}

export default function RightPanel() {
  const { activePipelines } = useJaxStore()
  const { t } = useI18n()
  const [tab, setTab] = useState('pipelines')
  const [cancelling, setCancelling] = useState(false)

  const pipelines = Object.values(activePipelines)
  const activePipeline = pipelines.find(p => ['running', 'waiting_gate'].includes(p.status))
    || pipelines[0]

  async function handleResume(pipelineId) {
    try {
      await api.post(`/pipelines/${pipelineId}/resume`)
    } catch (e) {
      console.error('resume failed', e)
    }
  }

  async function handleCancel(pipelineId) {
    setCancelling(true)
    try {
      await api.post(`/pipelines/${pipelineId}/cancel`)
    } catch (e) {
      console.error('cancel failed', e)
    } finally {
      setCancelling(false)
    }
  }

  const TABS = [
    { id: 'pipelines', label: t.tabDirectorJacobs },
    { id: 'log',       label: t.tabAudit },
  ]

  return (
    <div className="flex flex-col h-full bg-slate-900 border-l border-slate-700">
      {/* Tabs */}
      <div className="flex border-b border-slate-700">
        {TABS.map(({ id: tabId, label }) => (
          <button
            key={tabId}
            onClick={() => setTab(tabId)}
            className={`flex-1 py-2 text-xs uppercase tracking-wider font-semibold transition-colors ${
              tab === tabId
                ? 'text-blue-400 border-b-2 border-blue-400'
                : 'text-slate-500 hover:text-slate-300'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'pipelines' ? (
        <div className="flex-1 overflow-y-auto">
          {!activePipeline ? (
            <div className="px-4 py-6 text-sm text-slate-700 text-center">
              {t.noPipelinesActive}
            </div>
          ) : (
            <div className="p-3">
              <div className="mb-1">
                <div className="text-xs font-semibold text-slate-300 truncate">
                  {activePipeline.name}
                </div>
                <div className="text-xs text-slate-600 font-mono">
                  {activePipeline.pipeline_id?.slice(0, 12)}…
                </div>
                <div className={`text-xs mt-0.5 font-semibold ${
                  activePipeline.status === 'waiting_gate'
                    ? 'text-amber-400'
                    : activePipeline.status === 'running'
                    ? 'text-blue-400'
                    : 'text-slate-400'
                }`}>
                  {activePipeline.status}
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
                  className="mt-3 w-full py-2 rounded-lg bg-amber-500 hover:bg-amber-400 text-black text-xs font-bold uppercase tracking-widest transition-colors"
                >
                  {t.approve}
                </button>
              )}

              <button
                onClick={() => handleCancel(activePipeline.pipeline_id)}
                disabled={cancelling}
                className="mt-2 w-full py-1.5 rounded-lg bg-slate-800 hover:bg-red-900 border border-slate-700 hover:border-red-700 text-slate-500 hover:text-red-400 text-xs font-semibold transition-colors disabled:opacity-40"
              >
                {cancelling ? t.cancelling : t.cancelPipeline}
              </button>

              {pipelines.length > 1 && (
                <div className="mt-3 text-xs text-slate-600">
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
