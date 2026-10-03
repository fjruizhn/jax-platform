import { useState, useEffect, useRef, useCallback } from 'react'
import { useI18n, localeFor } from '../../i18n/index.jsx'
import { listarDocumentos, ocultarDocumento, restaurarDocumento, reprocesarDocumento, limitesDeDocumentos } from '../../api/proyectos'
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
// páginas de hasta 100, el máximo del backend) y reemplaza la lista por la
// respuesta. Así se ven los cambios de estado de cualquier fila cargada y las
// filas nuevas arriba, sin perder las páginas que la persona ya abrió.
// Tope: el sondeo re-pide a lo sumo TOPE_SONDEO filas (3 pedidos cada 5 s). Pasado
// ese número, las de más abajo se conservan tal cual y no se sondean: son las más
// viejas, y las que se procesan son las recientes. Sin tope, una lista larga
// multiplicaría los pedidos por la cantidad de páginas abiertas.
//
// Accesibilidad: una región `role="status"` (oculta a la vista) anuncia solo las
// TRANSICIONES de estado entre la lista anterior y la nueva; si nada cambió, calla.
//
// Reprocesar: botón en las filas sin_extractor o con error, solo de un tipo con extractor (las
// extensiones las manda el servidor en los límites, nunca una lista copiada aquí; se piden una vez,
// y solo cuando hay una fila reprocesable). Tampoco pide confirmación: no destruye nada, el
// original sigue en el servidor. La fila vuelve a «En espera» y el sondeo de arriba la sigue.
//
// Ocultar NO pide confirmación: es reversible con un clic (Ver ocultos →
// Restaurar) y no destruye nada. Pedirla en cada fila entorpecería ocultar
// varios y no protege de ningún daño.
const PASO_MS = 5000
const PAGINA = 50
const MAXIMO_BACKEND = 100
const TOPE_SONDEO = 300
const ACTIVOS = new Set(['en_cola', 'pendiente', 'procesando'])
// Estados desde los que el servidor deja reprocesar (si el tipo tiene extractor).
const REPROCESABLES = new Set(['sin_extractor', 'error'])
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
  const [anuncio, setAnuncio] = useState('')
  const [extensiones, setExtensiones] = useState(null) // tipos con extractor; null = sin saber (no se ofrece Reprocesar)
  const [latido, setLatido] = useState(0) // cuenta cada intento de refresco (rearma el sondeo)
  const pedidoRef = useRef(0)
  const ocupadoRef = useRef(false)
  const definitivoRef = useRef(false) // 403/404: no tiene sentido seguir sondeando
  const filasRef = useRef(null)
  filasRef.current = filas
  const siguienteRef = useRef(null)
  siguienteRef.current = siguiente
  const previasRef = useRef(null) // id → estado de la lista anterior, para anunciar transiciones
  // El texto se lee de una ref: un cambio de idioma no puede rehacer `refrescar` ni reiniciar la lista.
  const textosRef = useRef(T)
  textosRef.current = T

  const vistaEfectiva = vista === 'ocultos' && puedeOcultos ? 'ocultos' : 'visibles'

  const textoDeError = useCallback((err) => textosRef.current.errores[codigoDe(err)] ?? textosRef.current.errores.generico, [])

  // Trae `total` filas desde el principio, página a página, y las reemplaza.
  const refrescar = useCallback(async (visibles) => {
    const pedido = ++pedidoRef.current
    const total = Math.min(visibles, TOPE_SONDEO)
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
      if (visibles > total) {
        // Más filas que el tope: las de más abajo se conservan y su cursor sigue siendo el de antes.
        const ultima = acumuladas[acumuladas.length - 1]
        const cola = (filasRef.current ?? []).filter((f) => ultima && f.id < ultima.id)
        setFilas(acumuladas.concat(cola))
      } else {
        setFilas(acumuladas)
        setSiguiente(sig)
      }
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

  const visibles = () => Math.max(filasRef.current?.length ?? 0, PAGINA)

  // Carga inicial y cada cambio de proyecto o de vista.
  useEffect(() => {
    setFilas(null); setSiguiente(null); setError(null); setAnuncio('')
    previasRef.current = null
    definitivoRef.current = false
    refrescar(PAGINA)
    return () => { pedidoRef.current += 1 }
  }, [refrescar])

  // Sondeo: un temporizador por vez, solo mientras haya filas no terminales.
  const hayActivas = (filas ?? []).slice(0, TOPE_SONDEO).some((f) => ACTIVOS.has(f.estado))
  useEffect(() => {
    if (!hayActivas || definitivoRef.current) return undefined
    const timer = setTimeout(() => refrescar(visibles()), PASO_MS)
    return () => clearTimeout(timer)
  }, [hayActivas, latido, refrescar])

  // Anuncia las transiciones de estado (diff contra la lista anterior), una sola vez.
  useEffect(() => {
    if (filas === null) return
    const previas = previasRef.current
    previasRef.current = new Map(filas.map((f) => [f.id, f.estado]))
    if (previas === null) return
    const cambios = filas
      .filter((f) => previas.has(f.id) && previas.get(f.id) !== f.estado)
      .map((f) => T.anuncio(f.nombre, T.estados[f.estado] ?? f.estado))
    setAnuncio(cambios.join('. '))
  }, [filas]) // eslint-disable-line react-hooks/exhaustive-deps

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
      // Siempre: un refresco a mitad de la carga no puede dejar el botón deshabilitado.
      setCargandoMas(false)
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
    await refrescar(visibles())
    if (fallo) setError(fallo)
    ocupadoRef.current = false
    setOcupado(false)
  }

  const verOcultos = vistaEfectiva === 'ocultos'
  const hayReprocesables = puedeModificar && !verOcultos && (filas ?? []).some((f) => REPROCESABLES.has(f.estado))
  const limitesPedidosRef = useRef(false)
  useEffect(() => {
    if (!hayReprocesables || limitesPedidosRef.current) return
    limitesPedidosRef.current = true
    limitesDeDocumentos()
      .then((l) => setExtensiones(Array.isArray(l?.extensiones) ? l.extensiones : null))
      .catch(() => { limitesPedidosRef.current = false }) // sin límites no se ofrece; se reintenta en el siguiente refresco
  }, [hayReprocesables, filas])
  const sePuedeReprocesar = (f) =>
    puedeModificar && !verOcultos && REPROCESABLES.has(f.estado) && !!extensiones
    && extensiones.includes(String(f.tipo ?? '').toLowerCase())
  const vacio = filas !== null && filas.length === 0 && !error

  return (
    <div className="space-y-4">
      <p role="status" aria-live="polite" className="sr-only">{anuncio}</p>
      <div className="flex flex-wrap items-center justify-between gap-2">
        {puedeModificar ? (
          <div className="flex flex-wrap items-center gap-2">
            {agregando ? (
              <SelectorDeDocumentos proyectoId={id} nombreProyecto={proyecto.nombre}
                onTerminado={() => { setAgregando(false); refrescar(visibles()) }}
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
          <button type="button" onClick={() => refrescar(visibles())} className={BOTON}>{T.reintentar}</button>
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
                {/* Un correo largo se recorta (completo en el title) para que la fecha no baje de línea. */}
                <p className="flex min-w-0 whitespace-pre text-xs text-texto-tenue">
                  <span className="shrink-0">{formatoPeso(f.bytes ?? 0, locale, T.unidades)}</span>
                  {f.subido_por_email && (
                    <span className="min-w-0 truncate" title={f.subido_por_email}>{` · ${f.subido_por_email}`}</span>
                  )}
                  {fechaLegible(f.creado, locale) && <span className="shrink-0">{` · ${fechaLegible(f.creado, locale)}`}</span>}
                </p>
                {f.estado === 'error' && (
                  <p className="text-xs text-peligro break-words">{f.error ? T.causa(Object.hasOwn(T.causas, f.error) ? T.causas[f.error] : T.causas.desconocida) : T.sinMotivo}</p>
                )}
              </div>
              <span className={`text-xs ${COLOR_ESTADO[f.estado] ?? 'text-texto-tenue'}`}>{T.estados[f.estado] ?? f.estado}</span>
              {sePuedeReprocesar(f) && (
                <button type="button" disabled={ocupado} className={BOTON} aria-label={T.reprocesarDe(f.nombre)}
                  onClick={() => mutar(() => reprocesarDocumento(id, f.id))}>
                  {T.reprocesar}
                </button>
              )}
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
