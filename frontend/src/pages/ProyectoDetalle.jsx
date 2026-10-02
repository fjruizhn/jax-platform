import { useState, useEffect, useRef, useCallback } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useI18n } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
import { verProyecto, listarMiembros } from '../api/proyectos'
import { codigoDe } from '../api/errores'
import { TAMANO_BOTON_44 } from '../tema/botones'
import Miembros from '../components/proyectos/Miembros'
import Ajustes from '../components/proyectos/Ajustes'

// Detalle de proyecto (E1, T8): cabecera + pestañas Miembros / Ajustes.
// Un 404 llega igual para inexistente, oculto, deshabilitado y no miembro: la
// pantalla no dice por qué. El id de la ruta se valida ANTES de llamar a la API.
const FOCO = 'focus:outline-none focus-visible:ring-2 focus-visible:ring-foco'
const BOTON = `${TAMANO_BOTON_44} px-4 rounded bg-superficie-2 text-texto hover:text-texto-fuerte border border-borde-control ${FOCO} transition-colors`

function idValido(crudo) {
  const n = Number(crudo)
  return /^\d+$/.test(String(crudo ?? '')) && Number.isSafeInteger(n) && n > 0 ? n : null
}

export default function ProyectoDetalle() {
  const { t } = useI18n()
  const T = t.proyectos
  const id = idValido(useParams().id)
  const esAdmin = useJaxStore((s) => s.user?.role === 'superadmin')

  const [proyecto, setProyecto] = useState(null)
  const [miembros, setMiembros] = useState([])
  const [estado, setEstado] = useState(id === null ? 'noEncontrado' : 'cargando') // cargando | ok | noEncontrado | error
  const [error, setError] = useState(null)
  const [pestana, setPestana] = useState('miembros')
  const pedidoRef = useRef(0)
  const tabsRef = useRef({})

  const cargar = useCallback(async () => {
    if (id === null) return
    const pedido = ++pedidoRef.current
    try {
      const [p, m] = await Promise.all([verProyecto(id), listarMiembros(id)])
      if (pedido !== pedidoRef.current) return
      setProyecto(p)
      setMiembros(m.miembros ?? [])
      setEstado('ok')
      setError(null)
    } catch (err) {
      if (pedido !== pedidoRef.current) return
      if (err?.response?.status === 404) { setProyecto(null); setEstado('noEncontrado'); return }
      setError(T.errores[codigoDe(err)] ?? T.errores.generico)
      setEstado((previo) => (previo === 'ok' ? 'ok' : 'error'))
    }
  }, [id, T])

  useEffect(() => {
    setProyecto(null); setMiembros([]); setPestana('miembros')
    setEstado(id === null ? 'noEncontrado' : 'cargando')
    cargar()
    return () => { pedidoRef.current += 1 }
  }, [id, cargar])

  if (estado === 'noEncontrado') {
    return (
      <div className="min-h-dvh bg-fondo text-texto p-6">
        <div className="max-w-3xl mx-auto space-y-4">
          <p role="alert" className="text-sm text-texto">{T.noEncontrado}</p>
          <Link to="/proyectos" className={`${BOTON} no-underline`}>{T.volverAProyectos}</Link>
        </div>
      </div>
    )
  }

  const puedeAjustes = proyecto && (proyecto.papel === 'OWNER' || esAdmin)
  const pestanas = proyecto ? (puedeAjustes ? ['miembros', 'ajustes'] : ['miembros']) : []
  const activa = pestanas.includes(pestana) ? pestana : 'miembros'

  function teclado(e, i) {
    const salto = { ArrowRight: 1, ArrowLeft: -1 }[e.key]
    let destino = null
    if (salto) destino = (i + salto + pestanas.length) % pestanas.length
    else if (e.key === 'Home') destino = 0
    else if (e.key === 'End') destino = pestanas.length - 1
    if (destino === null) return
    e.preventDefault()
    setPestana(pestanas[destino])
    tabsRef.current[pestanas[destino]]?.focus()
  }

  return (
    <div className="min-h-dvh bg-fondo text-texto p-6">
      <div className="max-w-3xl mx-auto">
        <Link to="/proyectos" className={`${TAMANO_BOTON_44} -ml-4 text-texto-tenue hover:text-texto transition-colors gap-1.5 mb-4`}>
          <span aria-hidden="true">←</span>
          <span>{T.volverAProyectos}</span>
        </Link>

        {estado === 'cargando' && <p className="text-sm text-texto-tenue">{T.cargando}</p>}
        {estado === 'error' && (
          <div role="alert" className="flex items-center gap-3 text-sm text-peligro">
            <span>{error}</span>
            <button type="button" onClick={cargar} className={BOTON}>{T.reintentar}</button>
          </div>
        )}

        {proyecto && (
          <>
            <header className="mb-4">
              <h1 className="text-xl font-bold text-texto-fuerte break-words">{proyecto.nombre}</h1>
              <p className="mt-1 text-xs text-texto-tenue">
                {T.estados[proyecto.estado]} · {T.tuPapel}: {T.papeles[proyecto.papel]}
              </p>
              {proyecto.descripcion && <p className="mt-1 text-sm text-texto-suave break-words">{proyecto.descripcion}</p>}
            </header>

            {error && <p role="alert" className="mb-3 text-sm text-peligro">{error}</p>}

            <div role="tablist" aria-label={proyecto.nombre} className="flex gap-1 mb-4 border-b border-borde">
              {pestanas.map((p, i) => (
                <button key={p} type="button" role="tab" id={`tab-${p}`} aria-controls={`panel-${p}`}
                  aria-selected={activa === p} tabIndex={activa === p ? 0 : -1}
                  ref={(el) => { tabsRef.current[p] = el }}
                  onClick={() => setPestana(p)} onKeyDown={(e) => teclado(e, i)}
                  className={`${TAMANO_BOTON_44} px-4 -mb-px border-b-2 ${FOCO} transition-colors ${activa === p ? 'border-foco text-texto-fuerte' : 'border-transparent text-texto-tenue hover:text-texto'}`}>
                  {T[p]}
                </button>
              ))}
            </div>

            {pestanas.map((p) => (
              <div key={p} role="tabpanel" id={`panel-${p}`} aria-labelledby={`tab-${p}`} hidden={activa !== p}>
                {activa === p && (p === 'miembros'
                  ? <Miembros proyecto={proyecto} miembros={miembros} onCambio={cargar} />
                  : <Ajustes proyecto={proyecto} esAdmin={esAdmin} onCambio={cargar} />)}
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  )
}
