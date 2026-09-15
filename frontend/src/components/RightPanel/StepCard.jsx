import { memo } from 'react'
import { useJaxStore } from '../../store/useJaxStore'
import { useI18n } from '../../i18n/index.jsx'

const STATUS_COLORS = {
  pending:  'text-texto-tenue',
  running:  'text-info',
  waiting_gate: 'text-aviso',
  completed: 'text-exito',
  failed:   'text-peligro',
}

const STATUS_ICON = {
  pending:  '○',
  running:  '◌',
  waiting_gate: '⊙',
  completed: '●',
  failed:   '✗',
}

function StepCard({ step, pipelineId }) {
  const addMessage = useJaxStore((s) => s.addMessage)
  const { t } = useI18n()
  const colorClass = STATUS_COLORS[step.status] || 'text-texto-suave'
  const icon = STATUS_ICON[step.status] || '○'
  const isCompleted = step.status === 'completed'

  function handleExpand() {
    if (!isCompleted || !step.output) return
    addMessage({
      id: `step-detail-${step.step_id}-${Date.now()}`,
      facet: step.facet || 'jacobs',
      content: step.output,
      timestamp: new Date().toISOString(),
    })
  }

  return (
    <div
      className={`flex items-start gap-2 py-1.5 border-b border-borde last:border-0 ${
        isCompleted && step.output ? 'cursor-pointer hover:bg-superficie rounded' : ''
      }`}
      onClick={handleExpand}
      title={isCompleted && step.output ? t.clickToSeeResult : undefined}
    >
      <span className={`text-sm flex-shrink-0 mt-0.5 ${colorClass}`}>{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="text-xs text-texto truncate">{step.name}</div>
        {step.facet && (
          <div className="text-xs text-texto-tenue capitalize">{step.facet}</div>
        )}
        {isCompleted && step.output && (
          <div className="text-xs text-texto-tenue mt-0.5 truncate italic">
            {step.output.slice(0, 100)}{step.output.length > 100 ? '…' : ''}
          </div>
        )}
      </div>
      {step.duration_ms > 0 && (
        <span className="text-xs text-texto-tenue flex-shrink-0">
          {(step.duration_ms / 1000).toFixed(1)}s
        </span>
      )}
    </div>
  )
}

export default memo(StepCard)
