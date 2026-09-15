import { memo } from 'react'
import { useJaxStore } from '../../store/useJaxStore'

const TYPE_STYLES = {
  error:   'bg-peligro-fondo border-peligro-borde text-peligro',
  warning: 'bg-aviso-fondo border-aviso-borde text-aviso',
  info:    'bg-info-fondo border-accion text-info',
  success: 'bg-exito-fondo border-exito-borde text-exito',
}

function Toast() {
  const toasts = useJaxStore((s) => s.toasts)
  const dismissToast = useJaxStore((s) => s.dismissToast)

  if (toasts.length === 0) return null

  return (
    <div className="fixed top-4 right-4 z-50 space-y-2 max-w-sm">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`flex items-start gap-3 px-4 py-3 rounded-lg border text-sm font-medium shadow-xl ${
            TYPE_STYLES[toast.type] || TYPE_STYLES.info
          }`}
        >
          <span className="flex-1">{toast.message}</span>
          <button
            onClick={() => dismissToast(toast.id)}
            className="flex-shrink-0 opacity-60 hover:opacity-100 text-lg leading-none"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  )
}

export default memo(Toast)
