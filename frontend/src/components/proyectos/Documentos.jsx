import { useState, useEffect, useRef, useCallback } from 'react'
import { useI18n, localeFor } from '../../i18n/index.jsx'
import { listarDocumentos, ocultarDocumento, restaurarDocumento } from '../../api/proyectos'
import { codigoDe } from '../../api/errores'
import { TAMANO_BOTON_44 } from '../../tema/botones'
import SelectorDeDocumentos from './SelectorDeDocumentos'
import { formatoPeso } from './resumenDeLote'
import { puedeVerOcultos, puedeModificarDocumentos } from './permisos'

// Pestaña Documentos (E2a, T10). Lista paginada de `project_documents` con su
// estado; el estado lo manda el servidor (esta pantalla nunca lo calcula).
//
// Sondeo: mientras alguna fila VISIBLE esté en_cola, pendiente o procesando, se
// vuelve a pedir la lista cada 5 s, con un temporizador por vez (el siguiente se
// arma cuando termina la respuesta: dos pedidos nunca se solapan). Se cancela al
// desmontar y al cambiar de proyecto o de vista.
//
// El sondeo NO pisa la paginación: pide de nuevo tantas filas como se ven (por
// páginas de hasta 100, el máximo del backend) y reemplaza la lista entera por
// la respuesta. Así se ven los cambios de estado de cualquier fila cargada y las
// filas nuevas arriba, sin perder las páginas que la persona ya abrió.
//
// Ocultar NO pide confirmación: es reversible con un clic (Ver ocultos →
// Restaurar) y no destruye nada. Pedirla en cada fila entorpecería ocultar
// varios y no protege de ningún daño.
const PASO_MS = 5000
const PAGINA = 50
const MAXIMO_BACKEND = 100
const ACTIVOS = new Set(['en_cola', 'pendiente', 'procesando'])
// Texto de cada estado y color de token (solo tokens; nunca valores sueltos).
const COLOR_ESTADO = {
  en_cola: 'text-texto-tenue', pendiente: 'text-texto-tenue', procesando: 'text-texto-suave',
  listo: 'text-exito', parcial: 'text-aviso', error: 'text-peligro',
  sin_extractor: 'text-aviso', cancelado: 'text-texto-tenue',
}
const FOCO = 'focus:outline-none focus-visible:ring-2 focus-visible:ring-foco'
const BOTON = `${TAMANO_BOTON_44} rounded bg-superficie-2 text-texto hover:text-texto-fuerte border border-borde-control ${FOCO} disabled:opacity-50 transition-colors`

function fechaLegible(iso, locale) {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(locale)
}

export default function Documentos({ proyecto }) {
  const { t, lang } = useI18n()
  const T = t.proyectos.documentos
  const locale = localeFor(lang)
  const id = proyecto.id
  const puedeOcultos = puedeVerOcultos(proyecto)
  const puedeModificar = puedeModificarDocumentos(proyecto)

  const [vista, setVista] = useState('visibles') // visibles | ocultos
  const [filas, setFilas] = useState(null) // null = aún sin primera respuesta
  const [siguiente, setSiguiente] = useState(null)
  const [error, setError] = useState(null)
  const [cargandoMas, setCargandoMas] = useState(false)
  const [ocupado, setOcupado] = useState(false)
  const [agregando, setAgregando] = useState(false)
  const [latido, setLatido] = useState(0) // cuenta cada intento de refresco (rearma el sondeo)
  const pedidoRef = useRef(0)
  const ocupadoRef = useRef(false)
  const definitivoRef = useRef(false) // 403/404: no tiene sentido seguir sondeando
  const filasRef = useRef(null)
  filasRef.current = filas

  const vistaEfectiva = vista === 'ocultos' && puedeOcultos ? 'ocultos' : 'visibles'

  const textoDeError = useCallback((err) => T.errores[codigoDe(err)] ?? T.errores.generico, [T])

  // Trae `total` filas desde el principio, página a página, y las reemplaza.
  const refrescar = useCallback(async (total) => {
    const pedido = ++pedidoRef.current
    try {
      let acumuladas = []
      let cursor = null
      let sig = null
      do {
        const faltan = total - acumuladas.length
        const r = await listarDocumentos(id, { vista: vistaEfectiva, antesDe: cursor, limite: Math.min(MAXIMO_BACKEND, faltan) })
        if (pedido !== pedidoRef.current) return
        acumuladas = acumuladas.concat(r.documentos ?? [])
        sig = r.siguiente ?? null
        cursor = sig
      } while (sig !== null && acumuladas.length < total)
      setFilas(acumuladas)
      setSiguiente(sig)
      setError(null)
      definitivoRef.current = false
    } catch (err) {
      if (pedido !== pedidoRef.current) return
      const status = err?.response?.status
      definitivoRef.current = status === 403 || status === 404
      setError(textoDeError(err))
      setFilas((previas) => previas ?? [])
    } finally {
      if (pedido === pedidoRef.current) setLatido((n) => n + 1)
    }
  }, [id, vistaEfectiva, textoDeError])

  // Carga inicial y cada cambio de proyecto o de vista.
  useEffect(() => {
    setFilas(null); setSiguiente(null); setError(null)
    definitivoRef.current = false
    refrescar(PAGINA)
    return () => { pedidoRef.current += 1 }
  }, [refrescar])

  // Sondeo: un temporizador por vez, solo mientras haya filas no terminales.
  const hayActivas = (filas ?? []).some((f) => ACTIVOS.has(f.estado))
  useEffect(() => {
    if (!hayActivas || definitivoRef.current) return undefined
    const timer = setTimeout(() => refrescar(Math.max(filasRef.current?.length ?? 0, PAGINA)), PASO_MS)
    return () => clearTimeout(timer)
  }, [hayActivas, latido, refrescar])

  async function cargarMas() {
    if (siguiente === null || cargandoMas) return
    const pedido = pedidoRef.current
    setCargandoMas(true)
    try {
      const r = await listarDocumentos(id, { vista: vistaEfectiva, antesDe: siguiente, limite: PAGINA })
      if (pedido !== pedidoRef.current) return
      setFilas((previas) => (previas ?? []).concat(r.documentos ?? []))
      setSiguiente(r.siguiente ?? null)
      setError(null)
    } catch (err) {
      if (pedido === pedidoRef.current) setError(textoDeError(err))
    } finally {
      if (pedido === pedidoRef.current) setCargandoMas(false)
    }
  }

  // Una mutación a la vez. Éxito o fallo, se vuelve a pedir la lista al servidor.
  async function mutar(fn) {
    if (ocupadoRef.current) return
    ocupadoRef.current = true
    setOcupado(true)
    let fallo = null
    try {
      await fn()
    } catch (err) {
      fallo = textoDeError(err)
    }
    await refrescar(Math.max(filasRef.current?.length ?? 0, PAGINA))
    if (fallo) setError(fallo)
    ocupadoRef.current = false
    setOcupado(false)
  }

  const verOcultos = vistaEfectiva === 'ocultos'
  const vacio = filas !== null && filas.length === 0 && !error

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {puedeModificar ? (
          <div className="flex flex-wrap items-center gap-2">
            {agregando ? (
              <SelectorDeDocumentos proyectoId={id}
                onTerminado={() => { setAgregando(false); refrescar(Math.max(filasRef.current?.length ?? 0, PAGINA)) }}
                onCerrar={() => setAgregando(false)} />
            ) : (
              <button type="button" onClick={() => setAgregando(true)} className={BOTON}>{T.agregar}</button>
            )}
          </div>
        ) : <span />}
        {puedeOcultos && (
          <button type="button" aria-pressed={verOcultos} onClick={() => setVista(verOcultos ? 'visibles' : 'ocultos')}
            className={`${BOTON} ${verOcultos ? 'border-foco text-texto-fuerte' : ''}`}>
            {T.verOcultos}
          </button>
        )}
      </div>

      {error && (
        <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-peligro">
          <span>{error}</span>
          <button type="button" onClick={() => refrescar(Math.max(filasRef.current?.length ?? 0, PAGINA))} className={BOTON}>{T.reintentar}</button>
        </div>
      )}

      {filas === null && <p className="text-sm text-texto-tenue">{T.cargando}</p>}
      {vacio && <p className="text-sm text-texto-tenue">{verOcultos ? T.sinOcultos : T.vacio}</p>}

      {filas !== null && filas.length > 0 && (
        <ul aria-label={T.lista} className="space-y-2">
          {filas.map((f) => (
            <li key={f.id} className="flex flex-wrap items-center justify-between gap-3 bg-superficie border border-borde rounded-lg px-4 py-2">
              <div className="min-w-0 flex-1">
                <p className="text-sm text-texto-fuerte break-all">{f.nombre}</p>
                <p className="text-xs text-texto-tenue">
                  <span>{formatoPeso(f.bytes ?? 0, locale, T.unidades)}</span>
                  {f.subido_por_email && <span>{` · ${f.subido_por_email}`}</span>}
                  {fechaLegible(f.creado, locale) && <span>{` · ${fechaLegible(f.creado, locale)}`}</span>}
                </p>
                {f.estado === 'error' && (
                  <p className="text-xs text-peligro break-words">{f.error ? T.causa(f.error) : T.sinCausa}</p>
                )}
              </div>
              <span className={`text-xs ${COLOR_ESTADO[f.estado] ?? 'text-texto-tenue'}`}>{T.estados[f.estado] ?? f.estado}</span>
              {puedeModificar && (
                <button type="button" disabled={ocupado} className={BOTON}
                  aria-label={verOcultos ? T.restaurarDe(f.nombre) : T.ocultarDe(f.nombre)}
                  onClick={() => mutar(() => (verOcultos ? restaurarDocumento(id, f.id) : ocultarDocumento(id, f.id)))}>
                  {verOcultos ? T.restaurar : T.ocultar}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {siguiente !== null && (
        <button type="button" disabled={cargandoMas} onClick={cargarMas} className={BOTON}>{T.cargarMas}</button>
      )}
    </div>
  )
}
