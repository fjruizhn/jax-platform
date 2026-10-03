import { useState, useEffect, useRef, useMemo } from 'react'
import { useI18n, localeFor } from '../../i18n/index.jsx'
import Dialogo from '../Dialogo'
import { limitesDeDocumentos, subirDocumentos } from '../../api/proyectos'
import { codigoDe } from '../../api/errores'
import { TAMANO_BOTON_44 } from '../../tema/botones'
import { resumirLote, formatoPeso } from './resumenDeLote'

// Selector de documentos (E2a, T9). La persona elige archivos o una carpeta;
// ANTES de subir ve un resumen (cuántos, cuánto pesan, de qué tipos, cuáles se
// ignoran y por qué), confirma en una ventana propia (Dialogo) y sube con barra
// de avance. Un error del servidor se muestra dentro de la misma ventana, sin
// cerrarla y sin perder el resumen.
//
// Los topes y las extensiones los publica el backend: hasta que llegan, los
// botones de elegir están deshabilitados (nada se decide con valores fijos).
// El servidor sigue siendo la autoridad; el resumen solo evita subir lo que ya
// se sabe rechazado.
//
// `onTerminado(resultado)` se llama al cerrar la ventana de resultado (no al
// responder el servidor): así el padre puede refrescar su lista sin que la
// ventana desaparezca antes de que la persona lea lo que pasó. `onCerrar` se
// llama al cerrar sin subir (cancelar o Escape).
const FOCO = 'focus:outline-none focus-visible:ring-2 focus-visible:ring-foco'
const BOTON = `${TAMANO_BOTON_44} rounded bg-superficie-2 text-texto hover:text-texto-fuerte border border-borde-control ${FOCO} disabled:opacity-50 transition-colors`
const BOTON_PRIMARIO = `${TAMANO_BOTON_44} rounded bg-superficie-2 text-texto-fuerte border border-borde-control hover:border-foco ${FOCO} disabled:opacity-50 transition-colors`
const BOTON_SECUNDARIO = `${TAMANO_BOTON_44} rounded bg-superficie text-texto-suave hover:text-texto ${FOCO} transition-colors`
const CIFRA = 'text-right tabular-nums text-texto'
const MAX_ABIERTO = 5
// Tope de render por motivo: una carpeta enorme no puede crear miles de <li>.
const MAX_NOMBRES = 50

function agruparPorMotivo(ignorados) {
  const grupos = new Map()
  for (const { nombre, motivo } of ignorados) {
    if (!grupos.has(motivo)) grupos.set(motivo, [])
    grupos.get(motivo).push(nombre)
  }
  return [...grupos]
}

// Lo ignorado, agrupado por motivo: cantidad y nombres. Desplegado si son
// pocos, plegado si son muchos.
function Ignorados({ ignorados, T }) {
  if (!ignorados.length) return null
  return (
    <div className="space-y-1">
      <p className="text-xs text-aviso">{T.resumen.ignorados(ignorados.length)}</p>
      {agruparPorMotivo(ignorados).map(([motivo, nombres]) => (
        <details key={motivo} open={nombres.length <= MAX_ABIERTO}
          className="bg-hundido border border-borde rounded-lg px-3 py-1">
          <summary className={`cursor-pointer min-h-6 py-1 text-xs text-texto-suave ${FOCO}`}>
            {T.motivos[motivo] ?? T.resumen.motivoDesconocido} <span className="tabular-nums text-texto">({nombres.length})</span>
          </summary>
          <ul className="max-h-32 overflow-y-auto pb-1 space-y-0.5">
            {nombres.slice(0, MAX_NOMBRES).map((n, i) => (
              <li key={`${n}-${i}`} className="text-xs text-texto-suave break-all">{n || T.resumen.sinNombre}</li>
            ))}
          </ul>
          {nombres.length > MAX_NOMBRES && (
            <p className="text-xs text-texto-tenue pb-1">{T.resumen.yMas(nombres.length - MAX_NOMBRES)}</p>
          )}
        </details>
      ))}
    </div>
  )
}

function Resumen({ resumen, limites, T, locale }) {
  const tipos = Object.entries(resumen.porTipo).sort(([a], [b]) => a.localeCompare(b))
  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between gap-4 text-sm">
        <span className="text-texto">{T.resumen.archivos(resumen.aceptados.length)}</span>
        <span className="text-texto-suave tabular-nums">{T.resumen.peso(formatoPeso(resumen.totalBytes, locale, T.unidades))}</span>
      </div>
      {tipos.length > 0 && (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-texto-suave">
              <th scope="col" className="text-left font-normal pb-1">{T.resumen.columnaTipo}</th>
              <th scope="col" className="text-right font-normal pb-1">{T.resumen.columnaCantidad}</th>
            </tr>
          </thead>
          <tbody>
            {tipos.map(([ext, n]) => (
              <tr key={ext} className="border-t border-borde">
                <td className="py-1 text-texto-suave">.{ext}</td>
                <td className={`py-1 ${CIFRA}`}>{n}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Ignorados ignorados={resumen.ignorados} T={T} />
      {resumen.excedeLote === 'archivos' && (
        <p className="text-xs text-peligro">{T.resumen.excedeArchivos(limites.max_archivos_lote)}</p>
      )}
      {resumen.excedeLote === 'bytes' && (
        <p className="text-xs text-peligro">{T.resumen.excedeBytes(formatoPeso(limites.max_bytes_lote, locale, T.unidades))}</p>
      )}
      {resumen.excedeLote === null && resumen.aceptados.length === 0 && (
        <p className="text-xs text-peligro">{T.resumen.nadaQueSubir}</p>
      )}
    </div>
  )
}

function Avance({ T, fraccion }) {
  const porcentaje = fraccion === null ? undefined : Math.round(fraccion * 100)
  return (
    <div className="space-y-1">
      <p className="text-xs text-texto-suave tabular-nums">
        {T.subiendo}{porcentaje !== undefined && ` ${porcentaje} %`}
      </p>
      <div role="progressbar" aria-label={T.resumen.progreso} aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={porcentaje} className="h-2 w-full rounded bg-hundido border border-borde overflow-hidden">
        <div className={`h-full bg-foco ${porcentaje === undefined ? 'w-1/3 animate-pulse' : ''}`}
          style={porcentaje === undefined ? undefined : { width: `${porcentaje}%` }} />
      </div>
      <p className="text-xs text-texto-tenue">{T.resumen.noSeCierra}</p>
    </div>
  )
}

export default function SelectorDeDocumentos({ proyectoId, onTerminado, onCerrar }) {
  const { t, lang } = useI18n()
  const T = t.proyectos.documentos
  const locale = localeFor(lang)
  const [limites, setLimites] = useState(null)
  const [errorLimites, setErrorLimites] = useState(false)
  const [elegidos, setElegidos] = useState(null)
  const [subiendo, setSubiendo] = useState(false)
  const [fraccion, setFraccion] = useState(null)
  const [error, setError] = useState(null)
  const [resultado, setResultado] = useState(null)
  const subiendoRef = useRef(false)
  const vivoRef = useRef(true)
  // Resultado recibido y aún no entregado al padre; onTerminado siempre el último.
  const sinEntregarRef = useRef(null)
  const onTerminadoRef = useRef(onTerminado)
  onTerminadoRef.current = onTerminado
  const entradaArchivos = useRef(null)
  const entradaCarpeta = useRef(null)
  const botonActivo = useRef(null)
  const botonSubir = useRef(null)

  function cargarLimites() {
    setErrorLimites(false)
    limitesDeDocumentos()
      .then((l) => { if (vivoRef.current) setLimites(l) })
      .catch(() => { if (vivoRef.current) setErrorLimites(true) })
  }

  useEffect(() => {
    vivoRef.current = true
    cargarLimites()
    return () => {
      vivoRef.current = false
      // Si el padre desmonta con el resultado abierto, igual se le avisa.
      const r = sinEntregarRef.current
      if (r) { sinEntregarRef.current = null; onTerminadoRef.current?.(r) }
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const resumen = useMemo(() => (elegidos && limites ? resumirLote(elegidos, limites) : null), [elegidos, limites])
  const puedeSubir = !!resumen && resumen.aceptados.length > 0 && resumen.excedeLote === null && !subiendo

  // Dialogo enfoca el título al abrir; el foco inicial pedido es «Subir».
  useEffect(() => {
    if (resumen && !resultado) botonSubir.current?.focus()
  }, [!!resumen, !!resultado]) // eslint-disable-line react-hooks/exhaustive-deps

  function abrir(entrada, e) {
    botonActivo.current = e.currentTarget
    entrada.current?.click()
  }

  function alElegir(e) {
    const archivos = Array.from(e.target.files ?? [])
    e.target.value = '' // elegir lo mismo otra vez tiene que volver a disparar el cambio
    if (!archivos.length) return
    // El foco vuelve al botón que abrió el diálogo: Dialogo lo restaura al cerrar.
    botonActivo.current?.focus()
    setError(null)
    setResultado(null)
    setFraccion(null)
    setElegidos(archivos)
  }

  function cerrarSinSubir() {
    if (subiendoRef.current) return
    setElegidos(null)
    setError(null)
    onCerrar?.()
  }

  function cerrarResultado() {
    const r = resultado
    sinEntregarRef.current = null
    setResultado(null)
    setElegidos(null)
    onTerminado?.(r)
  }

  async function subir() {
    if (subiendoRef.current || !puedeSubir) return
    subiendoRef.current = true
    setSubiendo(true)
    setError(null)
    setFraccion(null)
    try {
      const r = await subirDocumentos(proyectoId, resumen.aceptados, {
        onProgreso: (f) => { if (vivoRef.current) setFraccion(f) },
      })
      if (vivoRef.current) { sinEntregarRef.current = r; setResultado(r) }
      else onTerminadoRef.current?.(r)
    } catch (err) {
      if (vivoRef.current) setError(T.errores[codigoDe(err)] ?? T.errores.generico)
    } finally {
      subiendoRef.current = false
      if (vivoRef.current) setSubiendo(false)
    }
  }

  const listo = !!limites

  return (
    <>
      <input ref={entradaArchivos} type="file" multiple hidden tabIndex={-1} aria-hidden="true" onChange={alElegir} />
      <input ref={entradaCarpeta} type="file" webkitdirectory="" hidden tabIndex={-1} aria-hidden="true" onChange={alElegir} />
      <div className="flex items-center gap-2">
        <button type="button" disabled={!listo} className={BOTON} onClick={(e) => abrir(entradaArchivos, e)}>
          {T.elegirArchivos}
        </button>
        <button type="button" disabled={!listo} className={BOTON} onClick={(e) => abrir(entradaCarpeta, e)}>
          {T.elegirCarpeta}
        </button>
        {errorLimites && (
          <>
            <p role="alert" className="text-xs text-peligro">{T.limites.error}</p>
            <button type="button" className={BOTON} onClick={cargarLimites}>{T.limites.reintentar}</button>
          </>
        )}
      </div>

      {resumen && resultado && (
        <Dialogo idTitulo="selector-documentos-titulo" titulo={T.resultado.titulo} onCerrar={cerrarResultado} className="max-w-lg">
          <div className="space-y-3">
            <p className="text-sm text-texto">{T.resultado.agregados(resultado.aceptados?.length ?? 0)}</p>
            <Ignorados ignorados={resultado.ignorados ?? []} T={T} />
            <div className="flex justify-end pt-2">
              <button type="button" onClick={cerrarResultado} className={BOTON_PRIMARIO}>{T.resultado.cerrar}</button>
            </div>
          </div>
        </Dialogo>
      )}

      {resumen && !resultado && (
        <Dialogo idTitulo="selector-documentos-titulo" titulo={T.resumen.titulo} onCerrar={cerrarSinSubir}
          cerrable={!subiendo} className="max-w-lg">
          <div className="space-y-3">
            <Resumen resumen={resumen} limites={limites} T={T} locale={locale} />
            {subiendo && <Avance T={T} fraccion={fraccion} />}
            {error && <p role="alert" className="text-xs text-peligro">{error}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={cerrarSinSubir} disabled={subiendo} className={BOTON_SECUNDARIO}>
                {T.resumen.cancelar}
              </button>
              <button ref={botonSubir} type="button" onClick={subir} disabled={!puedeSubir} aria-busy={subiendo}
                className={BOTON_PRIMARIO}>
                {T.resumen.confirmar}
              </button>
            </div>
          </div>
        </Dialogo>
      )}
    </>
  )
}
