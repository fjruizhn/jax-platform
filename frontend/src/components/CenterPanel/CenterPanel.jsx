import { memo, useRef, useEffect } from 'react'
import { useJaxStore } from '../../store/useJaxStore'
import { useI18n } from '../../i18n/index.jsx'
import Message from './Message'
import PanelEjecutor from '../Ejecutor/PanelEjecutor'
import { useEjecutor } from '../../store/useEjecutor'

function CenterPanel() {
  const messages = useJaxStore((s) => s.messages)
  const user = useJaxStore((s) => s.user)
  const { t } = useI18n()
  const bottomRef = useRef(null)
  // Modo Ejecutor (SP2, 2026-09-17): sólo superadmin; ocupa el centro.
  const ejecutor = useEjecutor((s) => s.activo) && user?.role === 'superadmin'

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, ejecutor])

  if (ejecutor) {
    return (
      <div className="flex flex-col flex-1 min-h-0 overflow-hidden bg-fondo">
        <PanelEjecutor />
      </div>
    )
  }

  return (
    <div className="flex flex-col flex-1 min-h-0 overflow-hidden bg-fondo">
      {/* Conversación: todo el alto del centro. El ojo HAL vive en el panel
          izquierdo desde el 2026-09-12. */}
      <div className="flex-1 overflow-y-auto px-4 pb-4 space-y-4">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-texto-tenue text-sm">
            <p>{t.inMemoryOf}</p>
            <p className="mt-1">{t.inHonorOf}</p>
          </div>
        ) : (
          messages.map((msg) => <Message key={msg.id} message={msg} />)
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}

export default memo(CenterPanel)
