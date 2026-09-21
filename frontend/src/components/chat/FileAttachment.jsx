import { useI18n } from '../../i18n/index.jsx'
import { TAMANO_MINIMO_TOQUE } from '../../tema/botones'

const ICONOS = { pdf: '📄', texto: '📝' }

export default function FileAttachment({ attachment, onRemove, uploading }) {
  const { t } = useI18n()
  if (!attachment && !uploading) return null
  const nombre = attachment?.nombre || t.altAttachment
  const esImagen = attachment?.tipo === 'imagen'

  return (
    <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-superficie border border-borde-control mb-2 max-w-xs">
      {esImagen ? (
        // RD4 (2026-09-17): vista previa desde el object URL local del
        // compositor (URL.createObjectURL del File elegido) -- nunca
        // mime+base64, el servidor no lo devuelve más.
        <img src={attachment.previewUrl} alt={nombre}
             className="w-10 h-10 rounded object-cover flex-shrink-0" />
      ) : (
        <span className="text-xl flex-shrink-0">{ICONOS[attachment?.origen] || '📎'}</span>
      )}
      <div className="flex-1 min-w-0">
        {attachment && <div className="text-xs text-texto truncate font-medium">{nombre}</div>}
        {/* vista_previa es texto acotado del servidor (nunca HTML): se pinta
            como nodo de texto de React, jamás con dangerouslySetInnerHTML. */}
        {attachment && !esImagen && attachment.vista_previa && (
          <div className="text-xs text-texto-suave truncate">{attachment.vista_previa}</div>
        )}
        {uploading && <div className="text-xs text-info">{t.attachUploading}</div>}
        {!uploading && attachment && <div className="text-xs text-exito">{t.attachReady}</div>}
        {!uploading && attachment?.recortado && <div className="text-xs text-aviso">{t.adjuntoRecortado}</div>}
      </div>
      {!uploading && (
        <button type="button" onClick={onRemove} title={t.attachRemove}
                className={`${TAMANO_MINIMO_TOQUE} flex-shrink-0 text-texto-tenue hover:text-peligro transition-colors text-sm font-bold`}>
          ×
        </button>
      )}
    </div>
  )
}
