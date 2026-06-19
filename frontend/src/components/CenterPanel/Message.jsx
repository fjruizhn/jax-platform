import ReactMarkdown from 'react-markdown'
import { useI18n } from '../../i18n/index.jsx'

const FACET_COLORS = {
  jax_local: '#3b82f6',
  jekyll:    '#6366f1',
  hyde:      '#f97316',
  hipatia:   '#10b981',
  thot:      '#f59e0b',
  kimi:      '#06b6d4',
  ada:       '#7c3aed',
  jacobs:    '#ffffff',
  dalle:     '#7c3aed',
  user:      '#94a3b8',
}

function Spinner({ color }) {
  return (
    <span
      className="inline-block w-3 h-3 rounded-full border-2 border-t-transparent animate-spin ml-1"
      style={{ borderColor: color, borderTopColor: 'transparent' }}
    />
  )
}

export default function Message({ message }) {
  const { t } = useI18n()
  const color = FACET_COLORS[message.facet] || '#94a3b8'
  const isUser = message.facet === 'user'
  const isRunning = message.status === 'running'

  return (
    <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      <div
        className="w-7 h-7 rounded-full flex-shrink-0 flex items-center justify-center text-xs font-bold mt-0.5"
        style={{ backgroundColor: color + '25', border: `1px solid ${color}60`, color }}
      >
        {(message.facet || 'U')[0].toUpperCase()}
      </div>
      <div className={`flex-1 max-w-[85%] ${isUser ? 'items-end' : 'items-start'} flex flex-col`}>
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs font-semibold capitalize" style={{ color }}>
            {message.facet === 'user' ? t.userLabel : (message.facet || t.userLabel)}
          </span>
          {isRunning && <Spinner color={color} />}
          <span className="text-xs text-slate-600">
            {message.timestamp ? new Date(message.timestamp).toLocaleTimeString('es-HN') : ''}
          </span>
        </div>
        <div
          className="rounded-lg px-3 py-2 text-sm text-slate-200 prose prose-invert prose-sm max-w-none"
          style={{
            backgroundColor: isUser ? '#1e3a5f' : '#1e293b',
            border: `1px solid ${isRunning ? color + '60' : color + '20'}`,
            opacity: isRunning ? 0.85 : 1,
          }}
        >
          {message.image_url && (
            <img
              src={message.image_url}
              alt={message.content || 'imagen generada'}
              className="rounded-md max-w-full mb-2"
              style={{ maxWidth: '512px', maxHeight: '400px', objectFit: 'contain' }}
            />
          )}
          {message.attachment && message.attachment.type === 'image' && message.attachment.base64 && (
            <img
              src={message.attachment.base64}
              alt={message.attachment.filename || 'adjunto'}
              className="rounded-md mb-2"
              style={{ maxWidth: '400px', maxHeight: '400px', objectFit: 'contain' }}
            />
          )}
          {message.attachment && message.attachment.type !== 'image' && (
            <div className="flex items-center gap-2 text-xs text-slate-400 mb-2 p-2 rounded bg-slate-800 border border-slate-700">
              <span>📎</span>
              <span>{message.attachment.filename}</span>
            </div>
          )}
          <ReactMarkdown>{message.content}</ReactMarkdown>
        </div>
      </div>
    </div>
  )
}
