import { NavLink, Link } from 'react-router-dom'
import { useI18n } from '../../i18n/index.jsx'
import { useNombreDelSistema } from '../../store/useApariencia'

// Orden pedido por Fernando (2026-10-02, con captura): Dashboard, Facetas &
// Modelos, Costos, Repositorio, Memoria, Proyectos, Usuarios, Pipelines
// ocultos, Configuración, Correo. (El de 2026-09-20 ponía Memoria 2ª.)
// Rutas relativas a /admin, salvo `a` (ruta absoluta de otra pantalla): Proyectos
// es la pantalla /proyectos que ya existe, con su propio "Volver" -- no se
// duplica dentro del caparazón de Administración.
// Íconos: emoji con presentación de color POR DEFECTO (Emoji_Presentation), que
// no dependen de un selector de variación; ⚙ y ✉ eran símbolos de texto y se
// veían grises.
const NAV_ITEMS = [
  { path: 'dashboard', labelKey: 'adminDashboard', icon: '◈' },
  { path: 'keys',      labelKey: 'adminFacetsModels', icon: '🧠' },
  { path: 'costs',     labelKey: 'adminCosts',     icon: '💰' },
  { path: 'repo',      labelKey: 'adminRepo',      icon: '📁' },
  { path: 'memoria',   labelKey: 'adminMemoria',   icon: '🧩' },
  { a: '/proyectos',   labelKey: 'adminProyectos', icon: '📂' },
  { path: 'users',     labelKey: 'adminUsers',     icon: '👤' },
  // Fix round 1 (MINOR-7, 2026-09-22): la ruta ya existía (Admin.jsx) y el
  // enlace de BarraUsuario ya llevaba acá, pero sin entrada en este menú el
  // superadmin no podía volver a "Pipelines ocultos" sin salir de
  // Administración y volver a hacer clic en el ícono de la barra.
  { path: 'pipelines-ocultos', labelKey: 'adminPipelinesOcultos', icon: '🙈' },
  { path: 'settings',  labelKey: 'adminSettings',  icon: '🔧' },
  { path: 'smtp',      labelKey: 'adminSmtp',      icon: '📧' },
]

export default function AdminSidebar() {
  const { t } = useI18n()
  const nombre = useNombreDelSistema(t)

  return (
    <aside className="w-52 flex-shrink-0 bg-fondo border-r border-borde flex flex-col">
      <div className="px-4 py-5 border-b border-borde">
        <div className="text-xs font-bold text-acento-texto uppercase tracking-widest">
          {t.adminTitle}
        </div>
        {/* Versión desde el archivo VERSION (__APP_VERSION__, vite.config.js); el
            nombre es system_name (frente C, 2026-09-16). */}
        <div className="text-xs text-texto-tenue mt-0.5">{t.adminVersion(nombre, __APP_VERSION__)}</div>
      </div>

      <nav className="flex-1 py-3">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.path ?? item.a}
            to={item.a ?? `/admin/${item.path}`}
            className={({ isActive }) =>
              `flex items-center gap-2.5 px-4 py-2.5 text-sm transition-colors ${
                isActive
                  ? 'bg-acento-fondo text-acento-texto border-r-2 border-acento'
                  : 'text-texto-suave hover:text-texto hover:bg-superficie'
              }`
            }
          >
            <span className="text-base">{item.icon}</span>
            <span>{t[item.labelKey]}</span>
          </NavLink>
        ))}
      </nav>

      <div className="px-4 py-4 border-t border-borde">
        <Link
          to="/"
          className="text-xs text-texto-tenue hover:text-texto transition-colors flex items-center gap-1.5"
        >
          <span>←</span>
          <span>{t.adminBack(nombre)}</span>
        </Link>
      </div>
    </aside>
  )
}
