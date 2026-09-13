import { useWebSocket } from '../store/useWebSocket'
import LeftPanel from '../components/LeftPanel/LeftPanel'
import CenterPanel from '../components/CenterPanel/CenterPanel'
import RightPanel from '../components/RightPanel/RightPanel'
import BottomBar from '../components/BottomBar/BottomBar'
import Toast from '../components/Notifications/Toast'
import LogoAxioma from '../components/LogoAxioma'
import BarraUsuario from '../components/BarraUsuario'

export default function Dashboard() {
  useWebSocket()

  return (
    <div className="flex flex-col h-dvh bg-hal-bg text-hal-text overflow-hidden">
      {/* Top bar */}
      <div className="flex-shrink-0 flex items-center justify-between px-4 py-2 bg-slate-900 border-b border-slate-700">
        <div className="flex items-center gap-3">
          <LogoAxioma />
        </div>
        <BarraUsuario />
      </div>

      {/* Main layout: 3 paneles */}
      <div className="flex flex-1 overflow-hidden">
        {/* Panel izquierdo — 20% */}
        <div className="w-1/5 flex-shrink-0 overflow-hidden">
          <LeftPanel />
        </div>

        {/* Panel central — 55% */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <CenterPanel />
          <BottomBar />
        </div>

        {/* Panel derecho — 25% */}
        <div className="w-1/4 flex-shrink-0 overflow-hidden">
          <RightPanel />
        </div>
      </div>

      {/* Toasts */}
      <Toast />
    </div>
  )
}
