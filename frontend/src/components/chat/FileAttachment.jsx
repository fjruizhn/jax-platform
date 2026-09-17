import { useI18n } from '../../i18n/index.jsx'

const ICONOS = { pdf: '📄', texto: '📝' }

export default function FileAttachment({ attachment, onRemove, uploading }) {
  const { t } = useI18n()
  if (!attachment && !uploading) return null
  const nombre = attachment?.nombre || t.altAttachment
  const esImagen = attachment?.tipo === 'imagen'

  return (
    <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-superficie border border-borde-control mb-2 max-w-xs">
      {esImagen ? (
        <img src={`data:${attachment.mime};base64,${attachment.base64}`} alt={nombre}
             className="w-10 h-10 rounded object-cover flex-shrink-0" />
      ) : (
        <span className="text-xl flex-shrink-0">{ICONOS[attachment?.origen] || '📎'}</span>
      )}
      <div className="flex-1 min-w-0">
        {attachment && <div className="text-xs text-texto truncate font-medium">{nombre}</div>}
        {uploading && <div className="text-xs text-info">{t.attachUploading}</div>}
        {!uploading && attachment && <div className="text-xs text-exito">{t.attachReady}</div>}
        {!uploading && attachment?.recortado && <div className="text-xs text-aviso">{t.adjuntoRecortado}</div>}
      </div>
      {!uploading && (
        <button type="button" onClick={onRemove} title={t.attachRemove}
                className="flex-shrink-0 text-texto-tenue hover:text-peligro transition-colors text-sm font-bold">
          ×
        </button>
      )}
    </div>
  )
}
