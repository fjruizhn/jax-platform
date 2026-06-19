import { NavLink, Link } from 'react-router-dom'
import { useI18n } from '../../i18n/index.jsx'

const NAV_ITEMS = [
  { path: 'dashboard', labelKey: 'adminDashboard', icon: '◈' },
  { path: 'keys',      labelKey: 'adminApiKeys',   icon: '🔑' },
  { path: 'users',     labelKey: 'adminUsers',     icon: '👤' },
  { path: 'repo',      labelKey: 'adminRepo',      icon: '📁' },
  { path: 'settings',  labelKey: 'adminSettings',  icon: '⚙' },
  { path: 'costs',     labelKey: 'adminCosts',     icon: '💰' },
]

export default function AdminSidebar() {
  const { t } = useI18n()

  return (
    <aside className="w-52 flex-shrink-0 bg-slate-900 border-r border-slate-800 flex flex-col">
      <div className="px-4 py-5 border-b border-slate-800">
        <div className="text-xs font-bold text-purple-400 uppercase tracking-widest">
          {t.adminTitle}
        </div>
        <div className="text-xs text-slate-600 mt-0.5">Axioma v0.3</div>
      </div>

      <nav className="flex-1 py-3">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.path}
            to={`/admin/${item.path}`}
            className={({ isActive }) =>
              `flex items-center gap-2.5 px-4 py-2.5 text-sm transition-colors ${
                isActive
                  ? 'bg-purple-900/40 text-purple-300 border-r-2 border-purple-500'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
              }`
            }
          >
            <span className="text-base">{item.icon}</span>
            <span>{t[item.labelKey]}</span>
          </NavLink>
        ))}
      </nav>

      <div className="px-4 py-4 border-t border-slate-800">
        <Link
          to="/"
          className="text-xs text-slate-500 hover:text-slate-300 transition-colors flex items-center gap-1.5"
        >
          <span>←</span>
          <span>{t.adminBack}</span>
        </Link>
      </div>
    </aside>
  )
}
