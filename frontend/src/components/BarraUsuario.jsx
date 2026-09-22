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

// Pieza de rompecabezas: la memoria son piezas sueltas hasta que alguien las
// revisa. Trazo en currentColor como el resto de la barra, para que AL DIA se
// vea como un icono mas y no como una alarma permanente.
function IconoPieza() {
  return (
    <svg className={ICONO} {...trazo}>
      <path d="M9 4h2a2 2 0 1 1 4 0h2a1 1 0 0 1 1 1v3a2 2 0 1 0 0 4v6a1 1 0 0 1-1 1h-4a2 2 0 1 0-4 0H5a1 1 0 0 1-1-1v-4a2 2 0 1 1 0-4V5a1 1 0 0 1 1-1z" />
    </svg>
  )
}

// Segundo contador (2026-09-21): flechas de intercambio, trazo de Lucide
// (licencia ISC), mismo criterio que el resto -- AL DIA se ve como un ícono
// más de la barra, no como una alarma permanente.
function IconoIntercambio() {
  return (
    <svg className={ICONO} {...trazo}>
      <path d="m16 3 4 4-4 4" />
      <path d="M4 7h16" />
      <path d="m8 21-4-4 4-4" />
      <path d="M20 17H4" />
    </svg>
  )
}

// Ojo tachado (Task 7, 2026-09-22): glifo propio, no de Lucide -- el mismo
// trazo en currentColor que el resto de la barra. Pipelines ocultos.
function IconoOculto() {
  return (
    <svg className={ICONO} {...trazo}>
      <path d="M2 12s3.5-7 10-7c2.1 0 3.9.6 5.4 1.5M22 12s-3.5 7-10 7c-2.1 0-3.9-.6-5.4-1.5" />
      <circle cx="12" cy="12" r="3" />
      <path d="M4 4l16 16" />
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

  // Segundo contador (2026-09-21, pedido de Fernando): propuestas de cambio
  // de modelo (`model_binding_proposal.status='pending'`) sin decidir. El
  // hueco que esto cierra: la propuesta #18 estuvo pendiente más de 6 horas y
  // sólo se vio porque se miró a mano -- nada avisaba. El endpoint no trae un
  // `total` aparte (a diferencia de /admin/memoria/hechos): se cuenta el
  // mismo array que ya lista AdminModelCatalog con este filtro
  // (?status=pending, pestaña "models" de /admin/keys).
  const [propuestasPendientes, setPropuestasPendientes] = useState(0)
  const etiquetaPropuestas = propuestasPendientes > 0
    ? t.barraPropuestasPendientes(propuestasPendientes)
    : t.barraPropuestasAlDia

  useEffect(() => {
    if (!esSuperadmin) return
    let vigente = true
    api.get('/admin/models/proposals', { params: { status: 'pending' } })
      .then((r) => { if (vigente) setPropuestasPendientes(r.data.proposals.length) })
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
            {/* Con pendientes, el emoji a color: resalta a proposito. Al dia,
                la pieza de trazo, que toma el token y se ve como sus hermanos.
                Pedido de Fernando: distinguirlo "de una sola vista". */}
            {sinVerificar > 0
              ? <span aria-hidden="true">🧩</span>
              : <IconoPieza />}
            <span className="text-xs font-bold">{sinVerificar}</span>
          </Link>
        )}

        {/* Segundo contador (2026-09-21): mismo patrón que el de memoria de
            arriba -- siempre visible, forma e ícono cambian con el estado.
            Lleva a /admin/keys?tab=models, pestaña "Catálogo de modelos"
            (AdminModelCatalog), que es donde se aprueba o rechaza cada
            propuesta -- de una sola vista, sin un clic más para encontrar la
            pestaña (AdminFacetsModels lee el `?tab` al montar). Sólo
            superadmin: es quien puede aprobar/rechazar y el único que entra
            a /admin/keys. */}
        {esSuperadmin && (
          <Link
            to="/admin/keys?tab=models"
            aria-label={etiquetaPropuestas}
            title={etiquetaPropuestas}
            className={`${BOTON} ${propuestasPendientes > 0 ? 'text-aviso hover:text-aviso' : 'hover:text-texto'}`}
          >
            {propuestasPendientes > 0
              ? <span aria-hidden="true">🔀</span>
              : <IconoIntercambio />}
            <span className="text-xs font-bold">{propuestasPendientes}</span>
          </Link>
        )}

        {/* Task 7 (2026-09-22, spec descartar-pipelines §5): mismo marcado que
            el enlace a /admin/memoria de arriba -- sin contador propio, a
            diferencia de memoria/propuestas: no hay un "pendiente" que contar
            acá, es sólo la vista. Sólo superadmin: es quien puede restaurar
            y el único que entra a /admin/pipelines-ocultos. */}
        {esSuperadmin && (
          <Link to="/admin/pipelines-ocultos" aria-label={t.pipelinesOcultosTitulo} title={t.pipelinesOcultosTitulo} className={BOTON_NEUTRO}>
            <IconoOculto />
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
