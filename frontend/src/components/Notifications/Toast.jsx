import { useJaxStore } from '../../store/useJaxStore'

const TYPE_STYLES = {
  error:   'bg-red-900 border-red-600 text-red-200',
  warning: 'bg-amber-900 border-amber-600 text-amber-200',
  info:    'bg-blue-900 border-blue-600 text-blue-200',
  success: 'bg-green-900 border-green-600 text-green-200',
}

export default function Toast() {
  const { toasts, dismissToast } = useJaxStore()

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
