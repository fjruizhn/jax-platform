import { useCallback, useEffect, useRef, useState } from 'react'
import { useJaxStore } from '../../store/useJaxStore'
import { useI18n } from '../../i18n/index.jsx'
import { listarProyectos, verProyecto } from '../../api/proyectos'
import { TAMANO_BOTON_ACCION, TAMANO_BOTON_44 } from '../../tema/botones'
import Dialogo from '../Dialogo'
import CrearProyectoModal from '../proyectos/CrearProyectoModal'
import SelectorDeDocumentos from '../proyectos/SelectorDeDocumentos'
import { puedeModificarDocumentos } from '../proyectos/permisos'

// Botón «Documentos del proyecto» de la barra del chat (E2a, T11; decisión C de
// Fernando). Vive junto al selector de proyecto, en Chat y Pipeline, con el
// tamaño de E1.1 (24 px). El de adjuntar del chat no cambia: los adjuntos de un
// mensaje siguen siendo cosa del chat.
//
// El store solo guarda {id, nombre} del proyecto activo, así que el papel y el
// estado se piden con verProyecto UNA vez por cambio de proyecto (no por render).
// Hasta que llegan, o si la consulta falla, el botón queda cerrado: nunca se
// ofrece subir sin saber el papel. Lo que llega solo vale si es del proyecto
// ACTUAL (un papel tardío de otro no se aplica). El servidor sigue siendo la
// autoridad; esto solo evita ofrecer lo que va a fallar.
//
// En «Personal» no hay a dónde subir: el botón abre una ventana para elegir (o
// crear) un proyecto y sigue al selector de documentos de ese proyecto.
const FOCO = 'focus:outline-none focus-visible:ring-2 focus-visible:ring-foco'
const BOTON = `${TAMANO_BOTON_ACCION} rounded bg-superficie-2 text-texto-suave hover:text-texto font-semibold ${FOCO} disabled:opacity-50 transition-colors`
const BOTON_LISTA = `${TAMANO_BOTON_44} w-full justify-start rounded bg-superficie-2 text-texto border border-borde-control hover:border-foco ${FOCO} transition-colors`
const BOTON_SECUNDARIO = `${TAMANO_BOTON_44} rounded bg-superficie text-texto-suave hover:text-texto ${FOCO} transition-colors`

function ElegirProyecto({ onElegido, onCrear, onCerrar }) {
  const { t } = useI18n()
  const T = t.proyectos.documentos.elegirProyecto
  const [lista, setLista] = useState(null)
  const [siguiente, setSiguiente] = useState(null)
  const [cargando, setCargando] = useState(false)
  const [error, setError] = useState(false)
  const vivoRef = useRef(true)
  const ocupadoRef = useRef(false)

  // Una página de activos; la lista nunca se trunca en silencio: mientras el
  // servidor diga que hay más, «Cargar más» sigue ahí. Se filtra por permiso
  // después de paginar, así que una página puede aportar 0 filas y seguir habiendo más.
  const cargar = useCallback(async (cursor) => {
    if (ocupadoRef.current) return
    ocupadoRef.current = true
    setCargando(true)
    setError(false)
    try {
      const r = await listarProyectos(cursor === null ? { vista: 'activos', limite: 100 } : { vista: 'activos', limite: 100, antesDe: cursor })
      if (!vivoRef.current) return
      const nuevos = (Array.isArray(r?.proyectos) ? r.proyectos : []).filter(puedeModificarDocumentos)
      setLista((previa) => (previa ?? []).concat(nuevos))
      setSiguiente(r?.siguiente ?? null)
    } catch {
      if (!vivoRef.current) return
      setError(true)
      setLista((previa) => previa ?? [])
    } finally {
      ocupadoRef.current = false
      if (vivoRef.current) setCargando(false)
    }
  }, [])

  useEffect(() => {
    vivoRef.current = true
    cargar(null)
    return () => { vivoRef.current = false }
  }, [cargar])

  return (
    <Dialogo idTitulo="elegir-proyecto-titulo" titulo={T.titulo} onCerrar={onCerrar}>
      <p className="text-xs text-texto-suave mb-3">{T.texto}</p>
      {lista === null && <p role="status" className="text-xs text-texto-suave mb-3">{t.proyectos.cargando}</p>}
      {error && <p role="alert" className="text-xs text-peligro mb-3">{t.proyectos.errores.generico}</p>}
      {lista !== null && !error && lista.length === 0 && siguiente === null && <p className="text-xs text-texto-suave mb-3">{T.ninguno}</p>}
      {lista !== null && lista.length > 0 && (
        <ul className="max-h-60 overflow-y-auto space-y-1 mb-3">
          {lista.map((p) => (
            <li key={p.id}>
              <button type="button" onClick={() => onElegido(p)} className={`${BOTON_LISTA} truncate`}>{p.nombre}</button>
            </li>
          ))}
        </ul>
      )}
      {lista !== null && siguiente !== null && (
        <button type="button" onClick={() => cargar(siguiente)} disabled={cargando} aria-busy={cargando}
          className={`${BOTON_SECUNDARIO} mb-3`}>{t.proyectos.cargarMas}</button>
      )}
      <div className="flex justify-between gap-2 pt-2">
        <button type="button" onClick={onCrear} className={BOTON_SECUNDARIO}>{T.crear}</button>
        <button type="button" onClick={onCerrar} className={BOTON_SECUNDARIO}>{t.proyectos.cancelar}</button>
      </div>
    </Dialogo>
  )
}

export default function BotonDocumentos() {
  const { t } = useI18n()
  const T = t.proyectos.documentos
  const proyectoActivo = useJaxStore((s) => s.proyectoActivo)
  const setProyectoActivo = useJaxStore((s) => s.setProyectoActivo)
  const idActivo = proyectoActivo?.id ?? null
  // {id, papel, estado} del proyecto consultado, o {id, error: true}; null = sin respuesta aún.
  const [detalle, setDetalle] = useState(null)
  // null | 'elegir' | 'crear' | { subir: proyectoId }
  const [paso, setPaso] = useState(null)
  // Número de la consulta vigente: una respuesta solo se aplica si sigue siendo la última
  // (cambiar de proyecto, o volver a consultar, deja sin efecto a la anterior).
  const pedidoRef = useRef(0)

  const consultar = useCallback((id) => {
    const pedido = ++pedidoRef.current
    verProyecto(id)
      .then((p) => { if (pedido === pedidoRef.current) setDetalle({ id, papel: p?.papel, estado: p?.estado }) })
      .catch(() => { if (pedido === pedidoRef.current) setDetalle({ id, error: true }) })
  }, [])

  // Cada cambio de proyecto descarta lo sabido del anterior (7→9→7 no reutiliza el papel viejo).
  useEffect(() => {
    setDetalle(null)
    if (idActivo === null) { pedidoRef.current += 1; return undefined }
    consultar(idActivo)
    return () => { pedidoRef.current += 1 }
  }, [idActivo, consultar])

  const conocido = idActivo !== null && detalle?.id === idActivo ? detalle : null
  let texto = T.boton
  let cerrado = false
  if (idActivo !== null) {
    if (!conocido) cerrado = true
    else if (conocido.error) { cerrado = true; texto = T.noSePudoComprobar }
    else if (conocido.estado !== 'ACTIVE') { cerrado = true; texto = T.proyectoArchivado }
    else if (!puedeModificarDocumentos(conocido)) { cerrado = true; texto = T.sinPermiso }
  }

  function abrir() {
    if (idActivo === null) { setPaso('elegir'); return }
    setPaso({ subir: idActivo })
    consultar(idActivo) // el papel o el estado pudieron cambiar desde la última vez
  }

  // Al cerrar el selector se vuelve a mirar: si el proyecto se archivó o se perdió el papel, el botón lo dice.
  function cerrarSelector() {
    setPaso(null)
    if (idActivo !== null) consultar(idActivo)
  }

  function seguirConProyecto(p) {
    setProyectoActivo(p)
    setPaso({ subir: p.id })
  }

  return (
    <>
      <button type="button" onClick={abrir} disabled={cerrado} aria-label={texto} title={texto}
        className={`${BOTON} ml-1`}>
        <span aria-hidden="true">📄</span>
      </button>
      {paso === 'elegir' && (
        <ElegirProyecto onElegido={seguirConProyecto} onCrear={() => setPaso('crear')} onCerrar={() => setPaso(null)} />
      )}
      {paso === 'crear' && (
        <CrearProyectoModal onCerrar={() => setPaso(null)} onCreado={seguirConProyecto} />
      )}
      {paso?.subir !== undefined && (
        <SelectorDeDocumentos proyectoId={paso.subir} onTerminado={cerrarSelector} onCerrar={cerrarSelector} />
      )}
    </>
  )
}
