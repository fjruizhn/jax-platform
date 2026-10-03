import { useEffect, useState } from 'react'
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
  const [error, setError] = useState(false)

  useEffect(() => {
    let vivo = true
    listarProyectos({ vista: 'activos', limite: 100 })
      .then((r) => { if (vivo) setLista((Array.isArray(r?.proyectos) ? r.proyectos : []).filter(puedeModificarDocumentos)) })
      .catch(() => { if (vivo) { setError(true); setLista([]) } })
    return () => { vivo = false }
  }, [])

  return (
    <Dialogo idTitulo="elegir-proyecto-titulo" titulo={T.titulo} onCerrar={onCerrar}>
      <p className="text-xs text-texto-suave mb-3">{T.texto}</p>
      {lista === null && <p role="status" className="text-xs text-texto-suave mb-3">{t.proyectos.cargando}</p>}
      {error && <p role="alert" className="text-xs text-peligro mb-3">{t.proyectos.errores.generico}</p>}
      {lista !== null && !error && lista.length === 0 && <p className="text-xs text-texto-suave mb-3">{T.ninguno}</p>}
      {lista !== null && lista.length > 0 && (
        <ul className="max-h-60 overflow-y-auto space-y-1 mb-3">
          {lista.map((p) => (
            <li key={p.id}>
              <button type="button" onClick={() => onElegido(p)} className={`${BOTON_LISTA} truncate`}>{p.nombre}</button>
            </li>
          ))}
        </ul>
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
  const [detalle, setDetalle] = useState(null)
  // null | 'elegir' | 'crear' | { subir: proyectoId }
  const [paso, setPaso] = useState(null)

  useEffect(() => {
    if (idActivo === null) return undefined
    let vivo = true
    verProyecto(idActivo)
      .then((p) => { if (vivo) setDetalle({ id: idActivo, papel: p?.papel, estado: p?.estado }) })
      .catch(() => { if (vivo) setDetalle(null) })
    return () => { vivo = false }
  }, [idActivo])

  const conocido = idActivo !== null && detalle?.id === idActivo ? detalle : null
  let texto = T.boton
  let cerrado = false
  if (idActivo !== null) {
    if (!conocido) cerrado = true
    else if (conocido.estado !== 'ACTIVE') { cerrado = true; texto = T.proyectoArchivado }
    else if (!puedeModificarDocumentos(conocido)) { cerrado = true; texto = T.sinPermiso }
  }

  function abrir() {
    setPaso(idActivo === null ? 'elegir' : { subir: idActivo })
  }

  function seguirConProyecto(p) {
    setProyectoActivo(p)
    setPaso({ subir: p.id })
  }

  return (
    <>
      <button type="button" onClick={abrir} disabled={cerrado} aria-label={texto} title={texto}
        className={`${BOTON} ml-1`}>
        <span aria-hidden="true">📎</span>
      </button>
      {paso === 'elegir' && (
        <ElegirProyecto onElegido={seguirConProyecto} onCrear={() => setPaso('crear')} onCerrar={() => setPaso(null)} />
      )}
      {paso === 'crear' && (
        <CrearProyectoModal onCerrar={() => setPaso(null)} onCreado={seguirConProyecto} />
      )}
      {paso?.subir !== undefined && (
        <SelectorDeDocumentos proyectoId={paso.subir} onTerminado={() => setPaso(null)} onCerrar={() => setPaso(null)} />
      )}
    </>
  )
}
