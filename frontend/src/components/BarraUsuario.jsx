import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useJaxStore } from '../store/useJaxStore'
import { useTema } from '../store/useTema'
import { useI18n } from '../i18n/index.jsx'
import MiCuentaModal from './MiCuentaModal'
import api from '../api/client'

// Barra superior derecha (2026-09-12, pedido de Fernando):
//   Usuario: <correo>  │  🌐 ES   ☀   ⚙ (solo superadmin)   ⏻
// Lo único que se lee es el usuario; el resto son íconos con nombre accesible
// y tooltip (i18n). Los íconos son de trazo en currentColor: toman el color
// del token de texto del botón (src/tema/tokens.css).

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

// Reloj con historial (Task 9, 2026-09-18): trazo de Lucide (licencia ISC),
// mismo criterio que el engranaje de arriba.
function IconoHistorial() {
  return (
    <svg className={ICONO} {...trazo}>
      <path d="M3 3v5h5" />
      <path d="M3.05 13A9 9 0 1 0 6 5.3L3 8" />
      <path d="M12 7v5l4 2" />
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
const BOTON = 'flex items-center gap-1 p-1.5 rounded text-texto-tenue transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-foco'
const BOTON_NEUTRO = `${BOTON} hover:text-texto`

export default function BarraUsuario() {
  const user = useJaxStore((s) => s.user)
  const logout = useJaxStore((s) => s.logout)
  // Logout en vuelo: el botón queda ocupado (un doble clic no envía dos).
  const saliendo = useJaxStore((s) => !!s.saliendo)
  const { theme, toggleTheme } = useTema()
  const { lang, setLang, t } = useI18n()
  const [miCuenta, setMiCuenta] = useState(false)

  // Contador de memoria sin verificar (2026-09-20, restricción dura): sólo
  // superadmin puede actuar sobre /admin/memoria y es el único que ve esta
  // pantalla -- pedirlo para cualquier otro rol sólo generaría 401 de más.
  // El `total` YA viene con el filtro correcto (SQL_CONTAR en
  // api/admin/memoria.py): is_verified=0 AND superseded_by IS NULL AND
  // (expires_at IS NULL OR expires_at > NOW()).
  const [sinVerificar, setSinVerificar] = useState(0)
  const esSuperadmin = user?.role === 'superadmin'
  // La etiqueta cambia con el estado: en 0 no es "0 hechos sin verificar"
  // (que se lee como un pendiente de cero) sino "al día" -- que es lo que
  // de verdad significa, y lo que un lector de pantalla tiene que decir.
  const etiquetaMemoria = sinVerificar > 0
    ? t.barraMemoriaSinVerificar(sinVerificar)
    : t.barraMemoriaAlDia

  useEffect(() => {
    if (!esSuperadmin) return
    let vigente = true
    api.get('/admin/memoria/hechos', { params: { verificado: false, limite: 1 } })
      .then((r) => { if (vigente) setSinVerificar(r.data.total) })
      .catch(() => {}) // fail-soft: sin el dato, el contador simplemente no aparece
    return () => { vigente = false }
  }, [esSuperadmin])

  const otroIdioma = lang === 'es' ? 'en' : 'es'
  const etiquetaIdioma = t.switchLanguage(NOMBRE_IDIOMA[otroIdioma])
  const etiquetaTema = theme === 'dark' ? t.lightMode : t.darkMode

  return (
    <div className="flex items-center gap-3 min-w-0">
      {/* Mi cuenta (etapa 4): el correo es el acceso. Sin aria-label, a propósito:
          el nombre accesible sigue siendo "Usuario: <correo>". */}
      <button
        type="button"
        onClick={() => setMiCuenta(true)}
        title={t.myAccount}
        className="text-xs text-texto-tenue truncate max-w-[20rem] rounded hover:text-texto transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-foco"
      >
        {t.userLabel}: <span className="text-texto">{user?.email}</span>
      </button>
      {miCuenta && <MiCuentaModal onCerrar={() => setMiCuenta(false)} />}

      <span aria-hidden="true" className="h-4 w-px bg-borde flex-shrink-0" />

      <div className="flex items-center gap-1 flex-shrink-0">
        <button type="button" onClick={() => setLang(otroIdioma)} aria-label={etiquetaIdioma} title={etiquetaIdioma} className={BOTON_NEUTRO}>
          <IconoGlobo />
          {/* La sigla en el texto real, no con CSS: es lo que lee un lector de pantalla. */}
          <span className="text-xs font-bold">{lang.toUpperCase()}</span>
        </button>

        <button type="button" onClick={toggleTheme} aria-label={etiquetaTema} title={etiquetaTema} className={BOTON_NEUTRO}>
          {theme === 'dark' ? <IconoSol /> : <IconoLuna />}
        </button>

        {/* Task 9: a diferencia del engranaje de abajo, esto lo ve CUALQUIER
            usuario logueado -- es su propio historial, no administración. */}
        <Link to="/historial" aria-label={t.historialTitle} title={t.historialTitle} className={BOTON_NEUTRO}>
          <IconoHistorial />
        </Link>

        {/* SIEMPRE visible, también en 0 (corrección de Fernando, 2026-09-20).
            Antes se ocultaba en 0 "para que cuando aparezca signifique algo",
            y el razonamiento estaba al revés: un indicador que sólo existe
            cuando hay problemas no deja saber si está funcionando. "0
            pendientes" es la información de que la memoria está al día.
            El color hace el trabajo que hacía la ausencia: apagado al día,
            `aviso` cuando hay algo esperando. Los dos tokens están vetados
            como texto sobre los fondos base en tema claro y oscuro
            (tokens.js::TEXTOS_SOBRE_BASE) -- no es un color elegido a ojo.
            Sólo superadmin: es quien puede actuar y el único que entra a
            /admin/memoria. */}
        {esSuperadmin && (
          <Link
            to="/admin/memoria"
            aria-label={etiquetaMemoria}
            title={etiquetaMemoria}
            className={`${BOTON} ${sinVerificar > 0 ? 'text-aviso hover:text-aviso' : 'hover:text-texto'}`}
          >
            <span aria-hidden="true">🧩</span>
            <span className="text-xs font-bold">{sinVerificar}</span>
          </Link>
        )}

        {esSuperadmin && (
          <Link to="/admin" aria-label={t.adminPanel} title={t.adminPanel} className={BOTON_NEUTRO}>
            <IconoEngranaje />
          </Link>
        )}

        <button
          type="button"
          onClick={() => logout()}
          disabled={saliendo}
          aria-busy={saliendo}
          aria-label={t.logout}
          title={t.logout}
          className={`${BOTON} hover:text-peligro disabled:opacity-50`}
        >
          <IconoSalir />
        </button>
      </div>
    </div>
  )
}
