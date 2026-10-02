import { useState, useEffect, useRef } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import { buscarCandidatos, invitarMiembro, cambiarPapel, quitarMiembro } from '../../api/proyectos'
import { codigoDe } from '../../api/errores'
import { TAMANO_BOTON_44 } from '../../tema/botones'
import ConfirmarAccion from './ConfirmarAccion'

// Pestaña Miembros (E1, T8). Controles solo para un OWNER con el proyecto
// ACTIVE; las filas `origen === 'TENANT_ADMIN'` nunca los muestran (el backend
// las protege con admin_protegido: la UI no ofrece lo que va a fallar). Toda
// mutación termina en `onCambio()`, que recarga proyecto y miembros desde la
// API: el estado local no se edita a mano.
const PAPELES = ['VIEWER', 'CONTRIBUTOR', 'OWNER']
const MIN_LETRAS = 2
const ESPERA_MS = 300
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
  const [seleccionado, setSeleccionado] = useState(null)
  const [candidatos, setCandidatos] = useState(null)
  const [papelNuevo, setPapelNuevo] = useState('VIEWER')
  const [ocupado, setOcupado] = useState(false)
  const ocupadoRef = useRef(false)
  const busquedaRef = useRef(0)

  // Debounce + descarte de respuestas tardías: cada cambio de la consulta
  // invalida la búsqueda anterior (contador espejo) y rearma el temporizador.
  useEffect(() => {
    const q = consulta.trim()
    const mia = ++busquedaRef.current
    if (!puedeGestionar || q.length < MIN_LETRAS || seleccionado) { setCandidatos(null); return undefined }
    const id = setTimeout(async () => {
      try {
        const r = await buscarCandidatos(proyecto.id, q)
        if (mia === busquedaRef.current) setCandidatos(r.candidatos ?? [])
      } catch (err) {
        if (mia !== busquedaRef.current) return
        setCandidatos(null)
        setError(T.errores[codigoDe(err)] ?? T.errores.generico)
      }
    }, ESPERA_MS)
    return () => clearTimeout(id)
  }, [consulta, seleccionado, puedeGestionar, proyecto.id, T])
  useEffect(() => () => { busquedaRef.current += 1 }, [])

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

  const emailInvitar = seleccionado ?? consulta.trim()

  async function invitar(e) {
    e.preventDefault()
    if (emailInvitar.length < 3) return
    const ok = await mutar(() => invitarMiembro(proyecto.id, { email: emailInvitar, papel: papelNuevo }))
    if (ok) { setConsulta(''); setSeleccionado(null); setCandidatos(null) }
  }

  return (
    <div className="space-y-4">
      {error && <p role="alert" className="text-sm text-peligro">{error}</p>}

      {puedeGestionar && (
        <form onSubmit={invitar} className="space-y-2">
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex-1 min-w-48">
              <label htmlFor="miembro-buscar" className="block text-xs text-texto-suave mb-1">{T.buscarPorEmail}</label>
              <input id="miembro-buscar" type="text" value={consulta} autoComplete="off"
                onChange={(e) => { setConsulta(e.target.value); setSeleccionado(null) }} className={CAMPO} />
            </div>
            <div>
              <label htmlFor="miembro-papel-nuevo" className="block text-xs text-texto-suave mb-1">{T.papel}</label>
              <select id="miembro-papel-nuevo" value={papelNuevo} onChange={(e) => setPapelNuevo(e.target.value)} className={SELECT}>
                {PAPELES.map((p) => <option key={p} value={p}>{T.papeles[p]}</option>)}
              </select>
            </div>
            <button type="submit" disabled={ocupado || emailInvitar.length < 3} className={BOTON}>{T.invitar}</button>
          </div>
          {candidatos !== null && (
            candidatos.length === 0
              ? <p className="text-xs text-texto-tenue">{T.sinCandidatos}</p>
              : (
                <ul aria-label={T.candidatosEncontrados} className="border border-borde rounded-lg bg-superficie divide-y divide-borde">
                  {candidatos.map((c) => (
                    <li key={c.user_id}>
                      <button type="button" onClick={() => { setSeleccionado(c.email); setConsulta(c.email); setCandidatos(null) }}
                        className={`w-full text-left min-h-11 px-3 text-sm text-texto hover:text-texto-fuerte ${FOCO}`}>
                        {c.email}
                      </button>
                    </li>
                  ))}
                </ul>
              )
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
