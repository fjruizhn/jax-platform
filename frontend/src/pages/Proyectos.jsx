import { useState, useEffect, useRef, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useI18n } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
import { useNombreDelSistema } from '../store/useApariencia'
import { listarProyectos } from '../api/proyectos'
import { codigoDe } from '../api/errores'
import { TAMANO_BOTON_ACCION } from '../tema/botones'
import CrearProyectoModal from '../components/proyectos/CrearProyectoModal'

// Lista de proyectos (E1, T7): pestañas Activos / Archivados / Ocultos, paginada
// con «Cargar más» (cursor `siguiente`). «Ocultos» solo se ofrece a un
// superadmin -- el mismo criterio con que el frontend muestra Admin; es solo
// interfaz: la defensa real es el 403/404 de la API al no-admin.
const VISTAS = ['activos', 'archivados', 'ocultos']
const FOCO = 'focus:outline-none focus-visible:ring-2 focus-visible:ring-foco'
const BOTON = `${TAMANO_BOTON_ACCION} min-h-11 px-4 rounded bg-superficie-2 text-texto hover:text-texto-fuerte border border-borde-control ${FOCO} disabled:opacity-50 transition-colors`

export default function Proyectos() {
  const { t } = useI18n()
  const nombreSistema = useNombreDelSistema(t)
  const esAdmin = useJaxStore((s) => s.user?.role === 'superadmin')
  const vistas = esAdmin ? VISTAS : VISTAS.filter((v) => v !== 'ocultos')

  const [vista, setVista] = useState('activos')
  const [proyectos, setProyectos] = useState([])
  const [siguiente, setSiguiente] = useState(null)
  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState(null)
  const [creando, setCreando] = useState(false)
  // Contador de pedidos: una respuesta tardía de otra pestaña (o de antes de
  // un reintento) no pisa la lista actual. La ref espeja de forma síncrona
  // lo que el estado de React no deja leer fuera de un render.
  const pedidoRef = useRef(0)

  const cargar = useCallback(async (v, antesDe) => {
    const pedido = ++pedidoRef.current
    setCargando(true)
    setError(null)
    if (antesDe === null) { setProyectos([]); setSiguiente(null) }
    try {
      const r = await listarProyectos({ vista: v, antesDe })
      if (pedido !== pedidoRef.current) return
      setProyectos((previos) => (antesDe === null ? r.proyectos : [...previos, ...r.proyectos]))
      setSiguiente(r.siguiente ?? null)
    } catch (err) {
      if (pedido !== pedidoRef.current) return
      setError(t.proyectos.errores[codigoDe(err)] ?? t.proyectos.errores.generico)
    } finally {
      if (pedido === pedidoRef.current) setCargando(false)
    }
  }, [t])

  useEffect(() => { cargar(vista, null) }, [vista, cargar])
  // Invalida lo pendiente al desmontar.
  useEffect(() => () => { pedidoRef.current += 1 }, [])

  return (
    <div className="min-h-dvh bg-fondo text-texto p-6">
      <div className="max-w-5xl mx-auto">
        <div className="flex items-center justify-between mb-6 gap-3">
          <h1 className="text-xl font-bold text-texto-fuerte">{t.proyectos.titulo}</h1>
          <div className="flex items-center gap-4">
            <button type="button" onClick={() => setCreando(true)} className={BOTON}>{t.proyectos.nuevo}</button>
            <Link to="/" className="text-xs text-texto-tenue hover:text-texto transition-colors flex items-center gap-1.5">
              <span aria-hidden="true">←</span>
              <span>{t.historialBack(nombreSistema)}</span>
            </Link>
          </div>
        </div>

        <div role="tablist" className="flex gap-1 mb-4 border-b border-borde">
          {vistas.map((v) => (
            <button key={v} type="button" role="tab" aria-selected={vista === v} onClick={() => setVista(v)}
              className={`${TAMANO_BOTON_ACCION} min-h-11 px-4 -mb-px border-b-2 ${FOCO} transition-colors ${vista === v ? 'border-foco text-texto-fuerte' : 'border-transparent text-texto-tenue hover:text-texto'}`}>
              {t.proyectos.vistas[v]}
            </button>
          ))}
        </div>

        {error && (
          <div role="alert" className="mb-4 flex items-center gap-3 text-sm text-peligro">
            <span>{error}</span>
            <button type="button" onClick={() => cargar(vista, siguiente !== null ? siguiente : null)} className={BOTON}>
              {t.proyectos.reintentar}
            </button>
          </div>
        )}

        {cargando && proyectos.length === 0 && !error && <p className="text-sm text-texto-tenue">{t.proyectos.cargando}</p>}
        {!cargando && !error && proyectos.length === 0 && <p className="text-sm text-texto-tenue">{t.proyectos.vacio}</p>}

        {proyectos.length > 0 && (
          <ul className="space-y-2">
            {proyectos.map((p) => (
              <li key={p.id}>
                <Link to={`/proyectos/${p.id}`} className={`block bg-superficie border border-borde rounded-lg px-4 py-3 hover:border-foco ${FOCO} transition-colors`}>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-sm font-medium text-texto-fuerte">{p.nombre}</span>
                    <span className="text-xs text-texto-tenue">{t.proyectos.papeles[p.papel]}</span>
                  </div>
                  {p.descripcion && <p className="mt-1 text-xs text-texto-suave">{p.descripcion}</p>}
                </Link>
              </li>
            ))}
          </ul>
        )}

        {siguiente !== null && !error && (
          <div className="mt-4">
            <button type="button" disabled={cargando} onClick={() => cargar(vista, siguiente)} className={BOTON}>
              {t.proyectos.cargarMas}
            </button>
          </div>
        )}
      </div>
      {creando && <CrearProyectoModal onCerrar={() => setCreando(false)} />}
    </div>
  )
}
