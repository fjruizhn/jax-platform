import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import { useI18n } from '../../i18n/index.jsx'
import { FACET_TOKENS } from '../../store/useJaxStore'
import { colorToken } from '../../tema/tokens'

// dalle/user no son facetas (ver useJaxStore.js): extensión local para el chat.
const TOKEN_DE = { ...FACET_TOKENS, dalle: 'faceta-imagen', user: 'texto-suave' }

function Spinner({ token }) {
  return (
    <span
      className="inline-block w-3 h-3 rounded-full border-2 border-t-transparent animate-spin ml-1"
      style={{ borderColor: colorToken(token), borderTopColor: 'transparent' }}
    />
  )
}

function Message({ message }) {
  const { t } = useI18n()
  const token = TOKEN_DE[message.facet] || 'texto-suave'
  const isUser = message.facet === 'user'
  const isRunning = message.status === 'running'

  return (
    <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      <div
        className="w-7 h-7 rounded-full flex-shrink-0 flex items-center justify-center text-xs font-bold mt-0.5 bg-superficie border"
        style={{ borderColor: colorToken(token, 0.38), color: colorToken(token) }}
      >
        {(message.facet || 'U')[0].toUpperCase()}
      </div>
      <div className={`flex-1 max-w-[85%] ${isUser ? 'items-end' : 'items-start'} flex flex-col`}>
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs font-semibold capitalize" style={{ color: colorToken(token) }}>
            {message.facet === 'user' ? t.userLabel : (message.facet || t.userLabel)}
          </span>
          {isRunning && <Spinner token={token} />}
          <span className="text-xs text-texto-tenue">
            {message.timestamp ? new Date(message.timestamp).toLocaleTimeString('es-HN') : ''}
          </span>
        </div>
        <div
          className={`rounded-lg px-3 py-2 text-sm text-texto max-w-none ${isUser ? 'bg-burbuja-usuario' : 'bg-superficie'}`}
          style={{
            border: `1px solid ${colorToken(token, isRunning ? 0.38 : 0.12)}`,
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
            <div className="flex items-center gap-2 text-xs text-texto-suave mb-2 p-2 rounded bg-hundido border border-borde">
              <span>📎</span>
              <span>{message.attachment.filename}</span>
            </div>
          )}
          <ReactMarkdown>{message.content}</ReactMarkdown>
          {message.contract_degraded && (
            <div className="text-xs text-texto-suave mt-2 italic">
              {t.contractDegradedNote}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default memo(Message)
