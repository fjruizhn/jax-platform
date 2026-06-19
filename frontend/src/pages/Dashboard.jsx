import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useJaxStore } from '../store/useJaxStore'
import { useWebSocket } from '../store/useWebSocket'
import { useTheme } from '../store/useTheme'
import { useI18n } from '../i18n/index.jsx'
import LeftPanel from '../components/LeftPanel/LeftPanel'
import CenterPanel from '../components/CenterPanel/CenterPanel'
import RightPanel from '../components/RightPanel/RightPanel'
import BottomBar from '../components/BottomBar/BottomBar'
import Toast from '../components/Notifications/Toast'

export default function Dashboard() {
  useWebSocket()
  const { user, logout } = useJaxStore()
  const { theme, toggleTheme } = useTheme()
  const { lang, setLang, t } = useI18n()

  return (
    <div className="flex flex-col h-screen bg-hal-bg text-hal-text overflow-hidden">
      {/* Top bar */}
      <div className="flex-shrink-0 flex items-center justify-between px-4 py-2 bg-slate-900 border-b border-slate-700">
        <div className="flex items-center gap-3">
          <span className="text-xs font-bold text-blue-400 uppercase tracking-widest">JAX</span>
          <span className="text-xs text-slate-600">|</span>
          <span className="text-xs text-slate-500">Platform v0.1</span>
          {user?.role === 'superadmin' && (
            <Link
              to="/admin"
              className="text-xs text-purple-400 hover:text-purple-300 transition-colors font-semibold"
            >
              {t.adminNav}
            </Link>
          )}
        </div>
        <div className="flex items-center gap-3">
          {/* Language selector */}
          <div className="flex gap-1">
            {['es', 'en'].map((l) => (
              <button
                key={l}
                onClick={() => setLang(l)}
                className={`px-1.5 py-0.5 rounded text-xs font-bold uppercase transition-colors ${
                  lang === l
                    ? 'bg-blue-600 text-white'
                    : 'text-slate-500 hover:text-slate-300'
                }`}
              >
                {l}
              </button>
            ))}
          </div>

          {/* Theme toggle */}
          <button
            onClick={toggleTheme}
            title={theme === 'dark' ? t.lightMode : t.darkMode}
            className="text-slate-500 hover:text-slate-300 transition-colors text-base leading-none"
          >
            {theme === 'dark' ? '☀' : '☾'}
          </button>

          <span className="text-xs text-slate-500">{user?.email}</span>
          <button
            onClick={logout}
            className="text-xs text-slate-600 hover:text-slate-400 transition-colors"
          >
            {t.logout}
          </button>
        </div>
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
