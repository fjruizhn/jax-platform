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
    <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-superficie border border-borde-control mb-2 max-w-xs">
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
        <div className="text-xs text-texto truncate font-medium">{attachment.filename}</div>
        {uploading && (
          <div className="text-xs text-info">{t.attachUploading}</div>
        )}
        {!uploading && attachment.ready && (
          <div className="text-xs text-exito">{t.attachReady}</div>
        )}
      </div>
      {!uploading && (
        <button
          type="button"
          onClick={onRemove}
          title={t.attachRemove}
          className="flex-shrink-0 text-texto-tenue hover:text-peligro transition-colors text-sm font-bold"
        >
          ×
        </button>
      )}
    </div>
  )
}
