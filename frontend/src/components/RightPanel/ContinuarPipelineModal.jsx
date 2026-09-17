import { useEffect, useRef, useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import Dialogo from '../Dialogo'
import AlertaError from '../AlertaError'
import ConfirmarCostoDialogo from '../ConfirmarCostoDialogo'
import CostoPorPaso from '../CostoPorPaso'
import { facetOptionsFor, cleanroomViolationsDePasos } from '../BottomBar/pipelineChain'
import { clasificarRechazo, textoDeDetalleDeMesa, textoDeErrorDeMesa, textoDeViolacion } from '../../api/errores'
import { formatearUsd } from '../../lib/moneda'
import { useConfirmacionDeCosto } from '../../lib/useConfirmacionDeCosto'

// Continuar un pipeline abortado o vencido (spec 2026-09-17 §6.2). Los pasos
// con resultado se reutilizan; los que faltan se pueden cambiar de faceta con
// las mismas opciones y el mismo clean-room que PipelineModal. Cada cambio
// vuelve a pedir el pre-vuelo de continue: Jacobs decide qué se reusa y cuánto
// cuesta lo que falta.
//
// Formas reales (adenda Task 10): `motivo` es null o un detail `{code, ...}`
// (se traduce como un rechazo); una reasignación inválida llega como 200
// continuable:false y se muestra en línea para corregir la faceta; el
// `veredicto` puede venir aunque no sea continuable (prevuelo_rechazado,
// limite_de_activos) y entonces se muestran sus violaciones y su costo.
//
// La confirmación de costo, la guardia contra el doble clic, el padre
// congelado bajo la confirmación y el foco de vuelta son los de PipelineModal
// (lib/useConfirmacionDeCosto); la clasificación de rechazos también
// (clasificarRechazo).
const CLASE_SELECT = 'text-xs bg-hundido border border-borde-control rounded px-2 py-1 text-texto focus:outline-none focus:border-foco disabled:opacity-40'

export default function ContinuarPipelineModal({ pipeline, onClose, onContinuado }) {
  const { t, lang } = useI18n()
  const id = pipeline.pipeline_id
  const [pasos, setPasos] = useState(null)
  const [capabilities, setCapabilities] = useState(null)
  const [errorCarga, setErrorCarga] = useState(false)
  const [reasignar, setReasignar] = useState({})
  const [estado, setEstado] = useState(null)
  const [errorPrevio, setErrorPrevio] = useState(null)
  // Mientras se recalcula tras una reasignación, la lista queda a la vista
  // (no parpadea) pero Continuar se bloquea y el costo viejo no se muestra.
  const [calculando, setCalculando] = useState(true)
  const [violaciones, setViolaciones] = useState([])
  const [errorEnvio, setErrorEnvio] = useState(null)
  const pedido = useRef(0)
  const {
    pendiente, abrirConfirmacion, cerrarConfirmacion, enviando, enviandoRef,
    bloqueado, siLibre, conGuardia, botonPrincipalRef,
  } = useConfirmacionDeCosto()

  useEffect(() => {
    let vigente = true
    Promise.all([api.get(`/pipelines/${id}`), api.get('/motors/capabilities')])
      .then(([p, c]) => {
        if (!vigente) return
        const lista = Array.isArray(p.data?.steps) ? p.data.steps : []
        setPasos([...lista].sort((a, b) => a.step_index - b.step_index))
        const porCapability = {}
        for (const cap of c.data?.capabilities || []) porCapability[cap.key] = cap.allowed_motors || []
        setCapabilities(porCapability)
      })
      .catch(() => { if (vigente) setErrorCarga(true) })
    return () => { vigente = false }
  }, [id])

  // Una respuesta vieja (de una reasignación anterior) no pisa la vigente. Lo
  // que dijo el intento anterior (violaciones al continuar, error) es de la
  // forma que se probó: al cambiar una faceta deja de valer.
  const claveReasignar = JSON.stringify(reasignar)
  useEffect(() => {
    const numero = ++pedido.current
    setCalculando(true)
    setErrorPrevio(null)
    setViolaciones([])
    setErrorEnvio(null)
    api.post(`/pipelines/${id}/continue/preflight`, { reasignar })
      .then(({ data }) => {
        if (numero !== pedido.current) return
        setEstado(data && typeof data === 'object' ? data : null)
        setCalculando(false)
      })
      .catch((err) => {
        if (numero !== pedido.current) return
        setErrorPrevio(textoDeErrorDeMesa(t, err, t.continuarErrorCarga))
        setCalculando(false)
      })
    // `reasignar` entra por su clave: el objeto cambia de identidad en cada set.
  }, [id, claveReasignar])

  const facetaDe = (p) => reasignar[String(p.step_index)] ?? p.facet
  const vigentes = (pasos || []).map((p) => ({
    facet: facetaDe(p), capability: p.capability, depends_on: Array.isArray(p.depends_on) ? p.depends_on : [],
  }))
  const cleanroom = cleanroomViolationsDePasos(vigentes)
  // El estado mostrado es el de la reasignación vigente sólo si ya llegó y no falló.
  const estadoVigente = !calculando && !errorPrevio ? estado : null
  const reusados = new Set(Array.isArray(estado?.pasos_reusados) ? estado.pasos_reusados : [])
  const veredicto = estadoVigente?.veredicto && typeof estadoVigente.veredicto === 'object' ? estadoVigente.veredicto : null
  const violacionesPrevio = veredicto && !veredicto.ok && Array.isArray(veredicto.violaciones) ? veredicto.violaciones : []
  const violacionesVisibles = violaciones.length > 0 ? violaciones : violacionesPrevio
  const noContinuable = estadoVigente && !estadoVigente.continuable
  const listo = pasos !== null && capabilities !== null
  const continuarDeshabilitado = enviando || !listo || !estadoVigente?.continuable || !veredicto?.ok || cleanroom.length > 0

  const elegir = siLibre((paso, faceta) => {
    if (enviandoRef.current) return
    setReasignar((r) => {
      const siguiente = { ...r }
      if (faceta === paso.facet) delete siguiente[String(paso.step_index)]
      else siguiente[String(paso.step_index)] = faceta
      return siguiente
    })
  })

  // Las mismas opciones que PipelineModal para esa capability. La faceta
  // original del plan queda siempre elegible (al final si el catálogo ya no la
  // ofrece), para poder volver a ella.
  function opciones(paso) {
    const delCatalogo = facetOptionsFor({ capability: paso.capability }, capabilities || {})
    return delCatalogo.includes(paso.facet) ? delCatalogo : [...delCatalogo, paso.facet]
  }

  // `costo` es el string costo_max_usd del veredicto confirmado, nunca un float.
  async function continuar(cuerpoReasignar, costo, previo) {
    await conGuardia(async () => {
      try {
        const cuerpo = costo == null ? { reasignar: cuerpoReasignar } : { reasignar: cuerpoReasignar, costo_confirmado_usd: costo }
        const { data } = await api.post(`/pipelines/${id}/continue`, cuerpo)
        onContinuado(data)
        onClose()
      } catch (err) {
        const r = clasificarRechazo(t, err, previo, t.continuarErrorCarga)
        if (r.tipo === 'costo') {
          abrirConfirmacion({ reasignar: cuerpoReasignar, veredicto: r.veredicto, aviso: r.aviso })
          return
        }
        cerrarConfirmacion()
        if (r.tipo === 'violaciones') setViolaciones(r.violaciones)
        else setErrorEnvio(r.texto)
      }
    })
  }

  function alPulsarContinuar() {
    if (bloqueado || enviandoRef.current || continuarDeshabilitado) return
    setViolaciones([])
    setErrorEnvio(null)
    if (veredicto.requiere_confirmacion) abrirConfirmacion({ reasignar, veredicto, aviso: null })
    else continuar(reasignar, null, null)
  }

  function confirmarCosto() {
    if (!pendiente) return
    continuar(pendiente.reasignar, pendiente.veredicto.costo_max_usd, pendiente.veredicto)
  }

  return (
    <Dialogo idTitulo="continuar-pipeline-titulo" titulo={t.continuarTitulo} onCerrar={onClose}
      cerrable={!bloqueado} className="max-w-lg">
      <div inert={bloqueado}>
        <p className="text-xs text-texto-tenue -mt-3 mb-4 truncate">{pipeline.name}</p>

        {errorCarga && <AlertaError className="mb-2 text-xs">{t.continuarErrorCarga}</AlertaError>}
        {!errorCarga && (!listo || (estado === null && !errorPrevio)) && (
          <p className="mb-2 text-xs text-texto-tenue">{t.continuarCargando}</p>
        )}

        {listo && estado && (
          <ol className="space-y-1.5 mb-3">
            {pasos.map((p) => (
              <li key={p.step_index} className="flex items-center gap-2 p-2 rounded-lg border border-borde bg-hundido">
                {reusados.has(p.step_index) ? (
                  <span className="text-xs text-exito">{t.continuarPasoReusado(p.step_index + 1, p.facet)}</span>
                ) : (
                  <>
                    <span className="text-xs text-texto flex-1">{t.continuarPasoACorrer(p.step_index + 1, p.capability)}</span>
                    <select aria-label={t.continuarPasoACorrer(p.step_index + 1, p.capability)} className={CLASE_SELECT}
                      value={facetaDe(p)} disabled={enviando} onChange={(e) => elegir(p, e.target.value)}>
                      {opciones(p).map((f) => <option key={f} value={f}>{f}</option>)}
                    </select>
                  </>
                )}
              </li>
            ))}
          </ol>
        )}

        {cleanroom.map((v) => (
          <p key={`${v.paso}-${v.dependsOn}`} className="mb-1 text-[11px] text-peligro">
            {t.continuarCleanroom(v.paso + 1, v.facet, v.dependsOn + 1)}
          </p>
        ))}

        {noContinuable && (
          <AlertaError className="mb-2 text-xs">
            {textoDeDetalleDeMesa(t, estadoVigente.motivo, t.continuarNoContinuable)}
          </AlertaError>
        )}

        {veredicto && (
          <div className="mb-2">
            {formatearUsd(veredicto.costo_max_usd, lang) !== null && (
              <p className="mb-1 text-xs text-texto">{t.continuarCostoMax(formatearUsd(veredicto.costo_max_usd, lang))}</p>
            )}
            <CostoPorPaso pasosCosto={veredicto.pasos_costo} className="space-y-1 mb-2" />
          </div>
        )}

        {violacionesVisibles.length > 0 && (
          <div role="alert" className="mb-2">
            <p className="text-[11px] font-semibold text-peligro mb-1">{t.prevueloTitulo}</p>
            {violacionesVisibles.map((v, i) => (
              <p key={i} className="text-[11px] text-peligro">{textoDeViolacion(t, v)}</p>
            ))}
          </div>
        )}

        {errorPrevio && <AlertaError className="mb-2 text-xs">{errorPrevio}</AlertaError>}
        {errorEnvio && <AlertaError className="mb-2 text-xs">{errorEnvio}</AlertaError>}

        <div className="flex gap-2">
          <button type="button" onClick={siLibre(onClose)}
            className="flex-1 py-2 rounded-lg text-xs font-semibold bg-hundido text-texto-suave hover:text-texto border border-borde transition-colors">
            {t.cancel}
          </button>
          <button type="button" ref={botonPrincipalRef} onClick={alPulsarContinuar} disabled={continuarDeshabilitado}
            className="flex-1 py-2 rounded-lg text-xs font-bold bg-accion hover:bg-accion-hover text-sobre-color transition-colors disabled:opacity-40">
            {t.continuarBoton}
          </button>
        </div>
      </div>

      {pendiente && (
        <ConfirmarCostoDialogo veredicto={pendiente.veredicto} enviando={enviando} aviso={pendiente.aviso}
          onConfirmar={confirmarCosto} onCancelar={cerrarConfirmacion} />
      )}
    </Dialogo>
  )
}
