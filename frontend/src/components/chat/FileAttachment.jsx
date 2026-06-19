import { useI18n } from '../../i18n/index.jsx'

const TYPE_ICONS = {
  image: '🖼',
  pdf: '📄',
  text: '📝',
  code: '💻',
}

export default function FileAttachment({ attachment, onRemove, uploading }) {
  const { t } = useI18n()
  if (!attachment) return null

  const icon = TYPE_ICONS[attachment.type] || '📎'
  const isImage = attachment.type === 'image'

  return (
    <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-800 border border-slate-600 mb-2 max-w-xs">
      {isImage && attachment.base64 ? (
        <img
          src={attachment.base64}
          alt={attachment.filename}
          className="w-10 h-10 rounded object-cover flex-shrink-0"
        />
      ) : (
        <span className="text-xl flex-shrink-0">{icon}</span>
      )}
      <div className="flex-1 min-w-0">
        <div className="text-xs text-slate-300 truncate font-medium">{attachment.filename}</div>
        {uploading && (
          <div className="text-xs text-blue-400">{t.attachUploading}</div>
        )}
        {!uploading && attachment.ready && (
          <div className="text-xs text-green-400">✓ listo</div>
        )}
      </div>
      {!uploading && (
        <button
          type="button"
          onClick={onRemove}
          title={t.attachRemove}
          className="flex-shrink-0 text-slate-500 hover:text-red-400 transition-colors text-sm font-bold"
        >
          ×
        </button>
      )}
    </div>
  )
}
