import { useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useI18n } from '../../i18n/index.jsx'
import Dialogo from '../Dialogo'
import { crearProyecto } from '../../api/proyectos'
import { codigoDe } from '../../api/errores'
import { TAMANO_BOTON_44 } from '../../tema/botones'

// Alta de proyecto (E1, T7), sobre Dialogo. La Idempotency-Key nace AL ABRIR
// el modal (este componente se monta al abrirlo) y se reutiliza en cada
// reintento con el mismo cuerpo: si la primera petición llegó al servidor y la respuesta se perdió,
// el reintento devuelve el mismo proyecto en vez de crear un duplicado.
// `enviandoRef` espeja el estado de forma síncrona: dos clics dentro del mismo
// render no pueden colarse mientras `disabled` aún no se pintó.
const CAMPO = 'w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto placeholder-texto-tenue focus:outline-none focus:border-foco'
const ETIQUETA = 'block text-xs text-texto-suave mb-1'
const BOTON_PRIMARIO = `${TAMANO_BOTON_44} rounded bg-superficie-2 text-texto-fuerte border border-borde-control hover:border-foco focus:outline-none focus-visible:ring-2 focus-visible:ring-foco disabled:opacity-50 transition-colors`
const BOTON_SECUNDARIO = `${TAMANO_BOTON_44} rounded bg-superficie text-texto-suave hover:text-texto focus:outline-none focus-visible:ring-2 focus-visible:ring-foco transition-colors`

export default function CrearProyectoModal({ onCerrar }) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const [nombre, setNombre] = useState('')
  const [descripcion, setDescripcion] = useState('')
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState(null)
  const enviandoRef = useRef(false)
  // Llave + cuerpo del último envío. Mismo cuerpo = reintento real (misma
  // llave); cuerpo distinto = otra solicitud (llave nueva), o la API respondería
  // idempotencia_conflicto y el usuario quedaría trabado.
  const envioRef = useRef({ llave: crypto.randomUUID(), cuerpo: null })
  const vivoRef = useRef(true)
  // Cierra la ventana de respuestas tardías: si el modal ya se cerró, el
  // resultado no navega ni toca el estado.
  const cerrar = () => { vivoRef.current = false; onCerrar() }

  async function enviar(e) {
    e.preventDefault()
    if (enviandoRef.current) return
    enviandoRef.current = true
    setEnviando(true)
    setError(null)
    try {
      const cuerpo = { nombre: nombre.trim(), descripcion: descripcion.trim() || null }
      const previo = envioRef.current
      if (previo.cuerpo && (previo.cuerpo.nombre !== cuerpo.nombre || previo.cuerpo.descripcion !== cuerpo.descripcion)) {
        envioRef.current = { llave: crypto.randomUUID(), cuerpo }
      } else {
        envioRef.current = { llave: previo.llave, cuerpo }
      }
      const creado = await crearProyecto(cuerpo, envioRef.current.llave)
      if (!vivoRef.current) return
      vivoRef.current = false
      onCerrar()
      navigate(`/proyectos/${Number(creado.id)}`)
    } catch (err) {
      if (!vivoRef.current) return
      setError(t.proyectos.errores[codigoDe(err)] ?? t.proyectos.errores.generico)
    } finally {
      enviandoRef.current = false
      if (vivoRef.current) setEnviando(false)
    }
  }

  return (
    <Dialogo idTitulo="crear-proyecto-titulo" titulo={t.proyectos.nuevo} onCerrar={cerrar}>
      <form onSubmit={enviar} className="space-y-3">
        <div>
          <label htmlFor="proyecto-nombre" className={ETIQUETA}>{t.proyectos.nombre}</label>
          <input id="proyecto-nombre" type="text" value={nombre} maxLength={255} required
            onChange={(e) => setNombre(e.target.value)} className={CAMPO} />
        </div>
        <div>
          <label htmlFor="proyecto-descripcion" className={ETIQUETA}>{t.proyectos.descripcionOpcional}</label>
          <textarea id="proyecto-descripcion" value={descripcion} maxLength={2000} rows={3}
            onChange={(e) => setDescripcion(e.target.value)} className={CAMPO} />
        </div>
        {error && <p role="alert" className="text-xs text-peligro">{error}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={cerrar} className={BOTON_SECUNDARIO}>{t.proyectos.cancelar}</button>
          <button type="submit" disabled={enviando} aria-busy={enviando} className={BOTON_PRIMARIO}>{t.proyectos.crear}</button>
        </div>
      </form>
    </Dialogo>
  )
}
