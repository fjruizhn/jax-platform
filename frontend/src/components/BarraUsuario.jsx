import { Link } from 'react-router-dom'
import { useJaxStore } from '../store/useJaxStore'
import { useTema } from '../store/useTema'
import { useI18n } from '../i18n/index.jsx'

// Barra superior derecha (2026-09-12, pedido de Fernando):
//   Usuario: <correo>  │  🌐 ES   ☀   ⚙ (solo superadmin)   ⏻
// Lo único que se lee es el usuario; el resto son íconos con nombre accesible
// y tooltip (i18n). Los íconos son de trazo en currentColor: toman los grises
// slate del resto de la UI, que html.light-mode ya ajusta en src/index.css.

// Cada idioma se nombra en su propia lengua (convención de los selectores de
// idioma): no se traduce.
const NOMBRE_IDIOMA = { es: 'Español', en: 'English' }

const ICONO = 'w-4 h-4'
const trazo = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round', viewBox: '0 0 24 24', 'aria-hidden': true }

function IconoGlobo() {
  return (
    <svg className={ICONO} {...trazo}>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z" />
    </svg>
  )
}

function IconoSol() {
  return (
    <svg className={ICONO} {...trazo}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  )
}

function IconoLuna() {
  return (
    <svg className={ICONO} {...trazo}>
      <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />
    </svg>
  )
}

// Engranaje y salida: trazos de Lucide (licencia ISC).
function IconoEngranaje() {
  return (
    <svg className={ICONO} {...trazo}>
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  )
}

function IconoSalir() {
  return (
    <svg className={ICONO} {...trazo}>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="M16 17l5-5-5-5" />
      <path d="M21 12H9" />
    </svg>
  )
}

// Sin color de hover en la base: dos hover:text-* en el mismo elemento no los
// decide el orden del className sino el del CSS generado. Cada botón pone el suyo.
const BOTON = 'flex items-center gap-1 p-1.5 rounded text-slate-500 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500'
const BOTON_NEUTRO = `${BOTON} hover:text-slate-300`

export default function BarraUsuario() {
  const user = useJaxStore((s) => s.user)
  const logout = useJaxStore((s) => s.logout)
  const { theme, toggleTheme } = useTema()
  const { lang, setLang, t } = useI18n()

  const otroIdioma = lang === 'es' ? 'en' : 'es'
  const etiquetaIdioma = t.switchLanguage(NOMBRE_IDIOMA[otroIdioma])
  const etiquetaTema = theme === 'dark' ? t.lightMode : t.darkMode

  return (
    <div className="flex items-center gap-3 min-w-0">
      <span className="text-xs text-slate-500 truncate max-w-[20rem]" title={user?.email}>
        {t.userLabel}: <span className="text-slate-300">{user?.email}</span>
      </span>

      <span aria-hidden="true" className="h-4 w-px bg-slate-700 flex-shrink-0" />

      <div className="flex items-center gap-1 flex-shrink-0">
        <button type="button" onClick={() => setLang(otroIdioma)} aria-label={etiquetaIdioma} title={etiquetaIdioma} className={BOTON_NEUTRO}>
          <IconoGlobo />
          {/* La sigla en el texto real, no con CSS: es lo que lee un lector de pantalla. */}
          <span className="text-xs font-bold">{lang.toUpperCase()}</span>
        </button>

        <button type="button" onClick={toggleTheme} aria-label={etiquetaTema} title={etiquetaTema} className={BOTON_NEUTRO}>
          {theme === 'dark' ? <IconoSol /> : <IconoLuna />}
        </button>

        {user?.role === 'superadmin' && (
          <Link to="/admin" aria-label={t.adminPanel} title={t.adminPanel} className={BOTON_NEUTRO}>
            <IconoEngranaje />
          </Link>
        )}

        <button
          type="button"
          onClick={logout}
          aria-label={t.logout}
          title={t.logout}
          className={`${BOTON} hover:text-red-400`}
        >
          <IconoSalir />
        </button>
      </div>
    </div>
  )
}
