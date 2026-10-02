import { useState, useEffect, useRef, useCallback } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import { buscarCandidatos, invitarMiembro, cambiarPapel, quitarMiembro } from '../../api/proyectos'
import { useJaxStore } from '../../store/useJaxStore'
import { codigoDe } from '../../api/errores'
import { TAMANO_BOTON_44 } from '../../tema/botones'
import ConfirmarAccion from './ConfirmarAccion'

// Pestaña Miembros (E1, T8). Controles solo para un OWNER con el proyecto
// ACTIVE; las filas `origen === 'TENANT_ADMIN'` nunca los muestran (el backend
// las protege con admin_protegido: la UI no ofrece lo que va a fallar). Toda
// mutación termina en `onCambio()`, que recarga proyecto y miembros desde la
// API: el estado local no se edita a mano.
const PAPELES = ['VIEWER', 'CONTRIBUTOR', 'OWNER']
const ESPERA_MS = 300
const TOPE_LISTA = 100
// Códigos con los que el servidor declara definitivo el fallo para ESE usuario:
// reintentarlo daría lo mismo, así que se desmarca. Cualquier otro fallo
// (500, genérico, desconocido) deja la casilla marcada, se vea o no.
const FALLO_DEFINITIVO = new Set(['ya_es_miembro', 'usuario_no_elegible', 'miembro_no_encontrado'])
const FOCO = 'focus:outline-none focus-visible:ring-2 focus-visible:ring-foco'
const CAMPO = `w-full min-h-11 bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto placeholder-texto-tenue ${FOCO}`
const SELECT = `min-h-11 bg-hundido border border-borde-control rounded-lg px-2 text-sm text-texto ${FOCO}`
const BOTON = `${TAMANO_BOTON_44} rounded bg-superficie-2 text-texto hover:text-texto-fuerte border border-borde-control ${FOCO} disabled:opacity-50 transition-colors`

export default function Miembros({ proyecto, miembros, onCambio }) {
  const { t } = useI18n()
  const T = t.proyectos
  const puedeGestionar = proyecto.papel === 'OWNER' && proyecto.estado === 'ACTIVE'

  const [error, setError] = useState(null)
  const [aQuitar, setAQuitar] = useState(null)
  const [consulta, setConsulta] = useState('')
  const [candidatos, setCandidatos] = useState(null)
  // Marcados: user_id -> email. Es un mapa y no una lista de ids para que un
  // marcado sobreviva a un filtro que ya no lo muestra.
  const [marcados, setMarcados] = useState({})
  const [papelNuevo, setPapelNuevo] = useState('VIEWER')
  const [resumen, setResumen] = useState(null)
  const [ocupado, setOcupado] = useState(false)
  const ocupadoRef = useRef(false)
  const busquedaRef = useRef(0)
  const primeraRef = useRef(true)

  // Pide la lista de elegibles con la consulta dada. Cada pedido invalida el
  // anterior (contador espejo): una respuesta tardía se descarta.
  const cargarCandidatos = useCallback(async (q) => {
    const mia = ++busquedaRef.current
    try {
      const r = await buscarCandidatos(proyecto.id, q)
      const lista = r.candidatos ?? []
      if (mia === busquedaRef.current) { setCandidatos(lista); return lista }
    } catch (err) {
      if (mia !== busquedaRef.current) return null
      setCandidatos(null)
      setError(T.errores[codigoDe(err)] ?? T.errores.generico)
    }
    return null
  }, [proyecto.id, T])

  // La lista se carga al montar sin escribir nada (q vacío); después, cada
  // cambio del buscador espera ESPERA_MS y rearma el temporizador.
  useEffect(() => {
    if (!puedeGestionar) { setCandidatos(null); return undefined }
    const espera = primeraRef.current ? 0 : ESPERA_MS
    primeraRef.current = false
    const id = setTimeout(() => cargarCandidatos(consulta.trim()), espera)
    // Al cambiar la consulta (o desmontar) se invalida lo que siga en vuelo.
    return () => { clearTimeout(id); busquedaRef.current += 1 }
  }, [consulta, puedeGestionar, cargarCandidatos])

  // Una mutación a la vez. Éxito o fallo, se recarga: si falló, el selector
  // vuelve al valor real del servidor.
  async function mutar(fn) {
    if (ocupadoRef.current) return false
    ocupadoRef.current = true
    setOcupado(true)
    setError(null)
    let ok = false
    try {
      await fn()
      ok = true
    } catch (err) {
      setError(T.errores[codigoDe(err)] ?? T.errores.generico)
    }
    try { await onCambio() } catch { /* la pantalla muestra su propio error de carga */ }
    ocupadoRef.current = false
    setOcupado(false)
    return ok
  }

  function alternar(c) {
    setMarcados((prev) => {
      const sig = { ...prev }
      if (sig[c.user_id]) delete sig[c.user_id]
      else sig[c.user_id] = c.email
      return sig
    })
  }

  const cantidad = Object.keys(marcados).length
  // Marcados que el filtro de ahora no muestra: se envían igual, así que se cuentan y se avisa.
  const visibles = new Set((candidatos ?? []).map((c) => String(c.user_id)))
  const ocultos = Object.keys(marcados).filter((id) => !visibles.has(id)).length

  // Una invitación por persona, en secuencia (el backend las trata una a una).
  // El guardia es el ref, no el estado: dos clics en el mismo tick no duplican.
  async function agregarSeleccionados(e) {
    e.preventDefault()
    if (cantidad === 0 || ocupadoRef.current) return
    const lote = Object.entries(marcados)
    // El lote queda atado a la sesión que lo empezó (ver `_sessionEpoch` en
    // useJaxStore): si se cierra sesión a mitad, los POST que quedan no salen
    // con la autoridad de quien entre después.
    const epoca = useJaxStore.getState()._sessionEpoch
    const mismaSesion = () => {
      const st = useJaxStore.getState()
      return st._sessionEpoch === epoca && !!st.token
    }
    ocupadoRef.current = true
    setOcupado(true)
    setError(null)
    setResumen(null)
    let agregados = 0
    const fallos = []
    let intentados = 0
    const quedan = { ...marcados }
    try {
      for (const [id, email] of lote) {
        if (!mismaSesion()) break
        intentados += 1
        try {
          await invitarMiembro(proyecto.id, { email, papel: papelNuevo })
          agregados += 1
          delete quedan[id]
        } catch (err) {
          const codigo = codigoDe(err)
          fallos.push({ email, texto: T.errores[codigo] ?? T.errores.generico })
          if (FALLO_DEFINITIVO.has(codigo)) delete quedan[id]
          const estado = err?.response?.status
          // 401/403: lo que sigue fallaría igual; no se siguen mandando.
          if (estado === 401 || estado === 403) break
        }
      }
      if (!mismaSesion()) return
      setResumen({ agregados, fallos, noEnviados: lote.length - intentados })
      try { await onCambio() } catch { /* la pantalla muestra su propio error de carga */ }
      await cargarCandidatos(consulta.trim())
      setMarcados(quedan)
    } catch {
      // algo inesperado: se avisa y el formulario no queda trabado
      setError(T.errores.generico)
    } finally {
      ocupadoRef.current = false
      setOcupado(false)
    }
  }

  return (
    <div className="space-y-4">
      {error && <p role="alert" className="text-sm text-peligro">{error}</p>}

      {puedeGestionar && (
        <form onSubmit={agregarSeleccionados} className="space-y-2">
          <div>
            <label htmlFor="miembro-buscar" className="block text-xs text-texto-suave mb-1">{T.buscarPorEmail}</label>
            <input id="miembro-buscar" type="text" value={consulta} autoComplete="off"
              onChange={(e) => setConsulta(e.target.value)} className={CAMPO} />
          </div>
          {candidatos !== null && (
            candidatos.length === 0
              ? <p className="text-xs text-texto-tenue">{T.sinCandidatos}</p>
              : (
                <ul aria-label={T.candidatosEncontrados} className="max-h-72 overflow-y-auto border border-borde rounded-lg bg-superficie divide-y divide-borde">
                  {candidatos.map((c) => (
                    <li key={c.user_id}>
                      <label className="flex items-center gap-3 min-h-11 px-3 text-sm text-texto hover:text-texto-fuerte cursor-pointer">
                        <input type="checkbox" checked={Boolean(marcados[c.user_id])} onChange={() => alternar(c)}
                          className={`w-4 h-4 ${FOCO}`} />
                        <span className="break-all">{c.email}</span>
                      </label>
                    </li>
                  ))}
                </ul>
              )
          )}
          {candidatos !== null && candidatos.length >= TOPE_LISTA && (
            <p className="text-xs text-texto-tenue">{T.mostrandoPrimeros100}</p>
          )}
          <div className="flex flex-wrap items-end gap-2">
            <div>
              <label htmlFor="miembro-papel-nuevo" className="block text-xs text-texto-suave mb-1">{T.papel}</label>
              <select id="miembro-papel-nuevo" value={papelNuevo} onChange={(e) => setPapelNuevo(e.target.value)} className={SELECT}>
                {PAPELES.map((p) => <option key={p} value={p}>{T.papeles[p]}</option>)}
              </select>
            </div>
            <button type="submit" disabled={ocupado || cantidad === 0} className={BOTON}>{T.agregarSeleccionados(cantidad)}</button>
            <p aria-live="polite" className="text-xs text-texto-suave pb-3">{ocultos > 0 ? T.seleccionadosOcultos(cantidad, ocultos) : T.seleccionados(cantidad)}</p>
          </div>
          {resumen && (
            <div role="status" className="text-sm text-texto">
              <p>{T.resumenAgregados(resumen.agregados)}</p>
              {resumen.noEnviados > 0 && <p>{T.noEnviados(resumen.noEnviados)}</p>}
              {resumen.fallos.length > 0 && (
                <ul className="text-peligro">
                  {resumen.fallos.map((f) => <li key={f.email}>{`${f.email}: ${f.texto}`}</li>)}
                </ul>
              )}
            </div>
          )}
        </form>
      )}

      <ul className="space-y-2">
        {miembros.map((m) => {
          const protegido = m.origen === 'TENANT_ADMIN'
          return (
            <li key={m.user_id} className="flex flex-wrap items-center justify-between gap-3 bg-superficie border border-borde rounded-lg px-4 py-2">
              <span className="text-sm text-texto-fuerte break-all">{m.email}</span>
              {puedeGestionar && !protegido ? (
                <div className="flex items-center gap-2">
                  <select aria-label={T.papelDe(m.email)} value={m.papel} disabled={ocupado} className={SELECT}
                    onChange={(e) => mutar(() => cambiarPapel(proyecto.id, m.user_id, e.target.value))}>
                    {PAPELES.map((p) => <option key={p} value={p}>{T.papeles[p]}</option>)}
                  </select>
                  <button type="button" disabled={ocupado} onClick={() => setAQuitar(m)} className={BOTON}>{T.quitar}</button>
                </div>
              ) : (
                <span className="text-xs text-texto-tenue">
                  {T.papeles[m.papel]}{protegido ? ` · ${T.origenProtegido}` : ''}
                </span>
              )}
            </li>
          )
        })}
      </ul>

      {aQuitar && (
        <ConfirmarAccion titulo={T.quitar} mensaje={T.confirmaQuitar(aQuitar.email)} textoConfirmar={T.quitar}
          onCancelar={() => setAQuitar(null)}
          onConfirmar={async () => { const m = aQuitar; setAQuitar(null); await mutar(() => quitarMiembro(proyecto.id, m.user_id)) }} />
      )}
    </div>
  )
}
