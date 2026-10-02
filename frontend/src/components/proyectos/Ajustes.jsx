import { useState, useEffect, useRef } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import { renombrarProyecto, cambiarEstado } from '../../api/proyectos'
import { codigoDe } from '../../api/errores'
import { TAMANO_BOTON_44 } from '../../tema/botones'
import ConfirmarAccion from './ConfirmarAccion'
import ConfirmacionSuma from '../ConfirmacionSuma'

// Pestaña Ajustes (E1, T8). Renombrar/archivar/restaurar son del OWNER;
// ocultar/mostrar del admin (criterio de interfaz: la defensa real es la API).
// Ocultar saca el proyecto de la vista de todos: pide ConfirmacionSuma.
const FOCO = 'focus:outline-none focus-visible:ring-2 focus-visible:ring-foco'
const CAMPO = `w-full min-h-11 bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto ${FOCO}`
const BOTON = `${TAMANO_BOTON_44} rounded bg-superficie-2 text-texto hover:text-texto-fuerte border border-borde-control ${FOCO} disabled:opacity-50 transition-colors`

export default function Ajustes({ proyecto, esAdmin, onCambio }) {
  const { t } = useI18n()
  const T = t.proyectos
  const esOwner = proyecto.papel === 'OWNER'
  const [nombre, setNombre] = useState(proyecto.nombre)
  const [descripcion, setDescripcion] = useState(proyecto.descripcion ?? '')
  const [error, setError] = useState(null)
  const [confirmando, setConfirmando] = useState(null) // 'archivar' | 'ocultar'
  const [ocupado, setOcupado] = useState(false)
  const ocupadoRef = useRef(false)

  // Tras recargar, el formulario refleja lo que dice el servidor.
  useEffect(() => { setNombre(proyecto.nombre); setDescripcion(proyecto.descripcion ?? '') }, [proyecto.nombre, proyecto.descripcion])

  async function mutar(fn) {
    if (ocupadoRef.current) return
    ocupadoRef.current = true
    setOcupado(true)
    setError(null)
    try {
      await fn()
    } catch (err) {
      setError(T.errores[codigoDe(err)] ?? T.errores.generico)
    }
    try { await onCambio() } catch { /* la pantalla muestra su propio error de carga */ }
    ocupadoRef.current = false
    setOcupado(false)
  }

  const guardar = (e) => {
    e.preventDefault()
    if (nombre.trim() === '') return
    mutar(() => renombrarProyecto(proyecto.id, { nombre: nombre.trim(), descripcion: descripcion.trim() || null }))
  }
  const poner = (estado) => async () => { setConfirmando(null); await mutar(() => cambiarEstado(proyecto.id, estado)) }

  return (
    <div className="space-y-6">
      {error && <p role="alert" className="text-sm text-peligro">{error}</p>}

      {esOwner && proyecto.estado === 'ACTIVE' && (
        <form onSubmit={guardar} className="space-y-3 max-w-xl">
          <div>
            <label htmlFor="ajuste-nombre" className="block text-xs text-texto-suave mb-1">{T.nombre}</label>
            <input id="ajuste-nombre" type="text" value={nombre} maxLength={255} onChange={(e) => setNombre(e.target.value)} className={CAMPO} />
          </div>
          <div>
            <label htmlFor="ajuste-descripcion" className="block text-xs text-texto-suave mb-1">{T.descripcion}</label>
            <textarea id="ajuste-descripcion" value={descripcion} maxLength={2000} rows={3} onChange={(e) => setDescripcion(e.target.value)} className={CAMPO} />
          </div>
          <button type="submit" disabled={ocupado || nombre.trim() === ''} className={BOTON}>{T.guardar}</button>
        </form>
      )}

      <div className="flex flex-wrap gap-2">
        {esOwner && proyecto.estado === 'ACTIVE' && (
          <button type="button" disabled={ocupado} onClick={() => setConfirmando('archivar')} className={BOTON}>{T.archivar}</button>
        )}
        {esOwner && proyecto.estado === 'ARCHIVED' && (
          <button type="button" disabled={ocupado} onClick={poner('ACTIVE')} className={BOTON}>{T.restaurar}</button>
        )}
        {esAdmin && proyecto.estado === 'ARCHIVED' && (
          <button type="button" disabled={ocupado} onClick={() => setConfirmando('ocultar')} className={BOTON}>{T.ocultar}</button>
        )}
        {esAdmin && proyecto.estado === 'HIDDEN' && (
          <button type="button" disabled={ocupado} onClick={poner('ARCHIVED')} className={BOTON}>{T.mostrar}</button>
        )}
      </div>

      {confirmando === 'archivar' && (
        <ConfirmarAccion titulo={T.archivar} mensaje={T.confirmaArchivar(proyecto.nombre)} textoConfirmar={T.archivar}
          onCancelar={() => setConfirmando(null)} onConfirmar={poner('ARCHIVED')} />
      )}
      {confirmando === 'ocultar' && (
        <ConfirmacionSuma titulo={T.ocultar} mensaje={T.confirmaOcultar(proyecto.nombre)} textoConfirmar={T.ocultar}
          onCancelar={() => setConfirmando(null)} onConfirmar={poner('HIDDEN')} />
      )}
    </div>
  )
}
