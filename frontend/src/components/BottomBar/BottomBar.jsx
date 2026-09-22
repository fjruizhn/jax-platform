import { memo, useState, useRef, useLayoutEffect, useEffect } from 'react'
import { useJaxStore } from '../../store/useJaxStore'
import { useEjecutor } from '../../store/useEjecutor'
import { useI18n } from '../../i18n/index.jsx'
import KillSwitch from './KillSwitch'
import PipelineModal from './PipelineModal'
import AttachButton from '../chat/AttachButton'
import FileAttachment from '../chat/FileAttachment'
import api from '../../api/client'
import { textoDeErrorDeMesa, textoDeAviso, codigoDe } from '../../api/errores'
import { cuerpoDeAdjunto, vistaDeAdjunto, faltaSoporteDeImagen } from '../chat/adjuntos'
import { alturaInput } from './alturaInput'
import { colorToken } from '../../tema/tokens'
import { TAMANO_BOTON_ACCION } from '../../tema/botones'
import { nombreDeFaceta } from '../../lib/nombreDeFaceta'

// Solo orden de despliegue — label viene de /api/state (display_name de la tabla
// `facet`, Bloque C) y el token de color del store; no se duplican aca.
const FACET_ORDER = ['jax_local', 'jekyll', 'hipatia', 'thot', 'kimi', 'hyde', 'ada']

function BottomBar() {
  const facetsState = useJaxStore((s) => s.facets)
  const FACETS = FACET_ORDER.map((id) => ({
    id,
    label: nombreDeFaceta(facetsState, id),
    token: facetsState[id]?.token || 'texto-suave',
  }))
  const [input, setInput] = useState('')
  const [modoBase, setModoBase] = useState('chat')
  // Modo Ejecutor (SP2, 2026-09-17): el flag vive en su store porque
  // CenterPanel también lo lee (muestra el panel en vez de los mensajes).
  // Sólo superadmin: si el rol cambia, el modo se apaga.
  const esSuperadmin = useJaxStore((s) => s.user?.role === 'superadmin')
  const ejecutorActivo = useEjecutor((s) => s.activo)
  const setEjecutorActivo = useEjecutor((s) => s.setActivo)
  const enviarEjecutor = useEjecutor((s) => s.enviar)
  const ejecutorContinua = useEjecutor((s) => s.misionActiva?.puede_continuar === true)
  const mode = esSuperadmin && ejecutorActivo ? 'ejecutor' : modoBase
  const [sending, setSending] = useState(false)
  const [showPipelineModal, setShowPipelineModal] = useState(false)
  const [pipelineObjective, setPipelineObjective] = useState('')
  const [attachment, setAttachment] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [politica, setPolitica] = useState(null)
  const [politicaFallo, setPoliticaFallo] = useState(false)
  const addMessage = useJaxStore((s) => s.addMessage)
  const updateMessage = useJaxStore((s) => s.updateMessage)
  const activeFacet = useJaxStore((s) => s.activeFacet)
  const setActiveFacet = useJaxStore((s) => s.setActiveFacet)
  const addToast = useJaxStore((s) => s.addToast)
  const registerPendingCommand = useJaxStore((s) => s.registerPendingCommand)
  const setGeneratingImage = useJaxStore((s) => s.setGeneratingImage)
  const { t } = useI18n()
  const textareaRef = useRef(null)
  // RD4 (2026-09-17): la vista previa de la imagen adjunta es un object URL
  // local (URL.createObjectURL del File elegido); este ref es el único dueño
  // de ESE URL puntual (el mensaje enviado se queda con el suyo propio, ver
  // adjuntos.js).
  const attachmentPreviewUrlRef = useRef(null)
  // Fix round 1 (review de ca8bb15): mirar solo "¿el modo ACTUAL es chat?"
  // no alcanza -- si el usuario sale del chat y vuelve antes de que la
  // subida resuelva, el modo de ahora vuelve a ser 'chat' y la respuesta
  // tardía se adjuntaba igual. uploadGenRef es un contador de generación:
  // se incrementa cada vez que el adjunto en curso deja de ser válido
  // (se sale del chat, se quita, se reemplaza por una subida nueva, se
  // desmonta). handleFileSelected captura el valor vigente ANTES de subir
  // y sólo adjunta si nadie lo movió mientras esperaba la red.
  const uploadGenRef = useRef(0)

  function descartarAdjuntoComposer() {
    uploadGenRef.current++
    if (attachmentPreviewUrlRef.current) {
      URL.revokeObjectURL(attachmentPreviewUrlRef.current)
      attachmentPreviewUrlRef.current = null
    }
    setAttachment(null)
  }

  // Al desmontar (p.ej. se sale de la pantalla de chat), un adjunto sin
  // enviar no debe dejar su object URL vivo para siempre, y una subida
  // todavía en vuelo no debe crear uno cuando responda tarde.
  useEffect(() => () => {
    uploadGenRef.current++
    if (attachmentPreviewUrlRef.current) URL.revokeObjectURL(attachmentPreviewUrlRef.current)
  }, [])

  useEffect(() => {
    if (!esSuperadmin && ejecutorActivo) setEjecutorActivo(false)
  }, [esSuperadmin, ejecutorActivo, setEjecutorActivo])

  function setMode(m) {
    setEjecutorActivo(m === 'ejecutor')
    if (m !== 'ejecutor') setModoBase(m)
    else setAttachment(null)
  }

  // La caja crece con el texto hasta MAX_LINEAS_INPUT y recién ahí hace scroll
  // (2026-09-12): antes quedaba en una línea y un prompt largo no se leía.
  // Mide lo real del textarea (altura de línea, padding, borde) en vez de
  // suponer píxeles; al enviar, `input` vuelve a '' y la caja a una línea.
  useLayoutEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    const cs = getComputedStyle(el)
    const px = (v) => parseFloat(v) || 0
    const { altoPx, conScroll } = alturaInput({
      scrollHeight: el.scrollHeight,
      lineHeight: px(cs.lineHeight) || 20,
      paddingY: px(cs.paddingTop) + px(cs.paddingBottom),
      bordeY: px(cs.borderTopWidth) + px(cs.borderBottomWidth),
    })
    el.style.height = `${altoPx}px`
    el.style.overflowY = conScroll ? 'auto' : 'hidden'
  }, [input])

  // Frente D: qué se puede adjuntar lo dice el servidor. Si no responde, no
  // se adjunta (fail-closed) y el botón lo explica.
  useEffect(() => {
    let vivo = true
    api.get('/chat/adjuntos')
      .then(({ data }) => { if (vivo) setPolitica(data) })
      .catch(() => { if (vivo) setPoliticaFallo(true) })
    return () => { vivo = false }
  }, [])

  const MODES = [
    { id: 'chat',     label: t.modeChat },
    { id: 'comando',  label: t.modeComando },
    { id: 'pipeline', label: t.modePipeline },
    { id: 'imagen',   label: t.modeImagen },
    ...(esSuperadmin ? [{ id: 'ejecutor', label: t.ejecutor.modo }] : []),
  ]

  const activeFacetObj = FACETS.find((f) => f.id === activeFacet) || FACETS[0]
  const placeholder = mode === 'chat' ? t.placeholderChat(activeFacetObj.label)
    : mode === 'comando' ? t.placeholderComando()
    : mode === 'pipeline' ? t.placeholderPipeline()
    : mode === 'imagen' ? t.placeholderImagen()
    : mode === 'ejecutor' ? (ejecutorContinua ? t.ejecutor.placeholderTurno : t.ejecutor.placeholderNueva)
    : ''

  // A-43 (2026-09-16): los tres errores de la Mesa se arman igual. El prefijo
  // es una clave de i18n; el detalle ya viene traducido (textoDeErrorDeMesa).
  function agregarError(facet, id, prefijoClave, detalle) {
    addMessage({ id, facet, content: `**${t[prefijoClave]}:** ${detalle}`, timestamp: new Date().toISOString() })
  }

  async function handleFileSelected(file) {
    if (politica && file.size > politica.max_bytes) {
      addToast({ message: t.erroresMesa.adjunto_demasiado_grande({ max_bytes: politica.max_bytes }), type: 'error' })
      return
    }
    // Reemplazo o subida nueva: invalida cualquier subida anterior todavía
    // en vuelo (ver uploadGenRef arriba).
    uploadGenRef.current++
    const miGeneracion = uploadGenRef.current
    setUploading(true)
    const formData = new FormData()
    formData.append('file', file)
    try {
      // A-21: sin Content-Type a mano -- el navegador pone el boundary.
      const { data } = await api.post('/chat/upload', formData)
      // Generación vieja: se salió del chat (y volvió o no), se quitó el
      // adjunto, se reemplazó por otra subida, o el componente se
      // desmontó. El chequeo va ANTES de crear el object URL -- nunca se
      // crea uno para descartarlo enseguida.
      if (uploadGenRef.current !== miGeneracion) return
      if (attachmentPreviewUrlRef.current) URL.revokeObjectURL(attachmentPreviewUrlRef.current)
      const previewUrl = data.tipo === 'imagen' ? URL.createObjectURL(file) : null
      attachmentPreviewUrlRef.current = previewUrl
      setAttachment({ ...data, archivo: file, previewUrl })
    } catch (err) {
      addToast({ message: textoDeErrorDeMesa(t, err, t.attachError), type: 'error' })
    } finally {
      setUploading(false)
    }
  }

  function elegirModo(m) {
    setMode(m)
    // Solo el chat manda adjuntos: al salir se quitan, no se pierden callados.
    if (m !== 'chat') descartarAdjuntoComposer()
  }

  const imagenSinSoporte = mode === 'chat' && faltaSoporteDeImagen(attachment, politica, activeFacet)

  async function handleSend() {
    const text = input.trim()
    if (!text || sending || imagenSinSoporte) return

    if (mode === 'pipeline') {
      setPipelineObjective(text)
      setInput('')
      setShowPipelineModal(true)
      return
    }

    setSending(true)
    setInput('')

    // Ejecutor: nada va al chat. Si el backend no la acepta, el texto vuelve a
    // la caja y el error se ve traducido en el panel.
    if (mode === 'ejecutor') {
      const aceptada = await enviarEjecutor(text)
      if (!aceptada) setInput(text)
      setSending(false)
      textareaRef.current?.focus()
      return
    }

    addMessage({
      id: Date.now().toString(),
      facet: 'user',
      content: text,
      attachment: mode === 'chat' && attachment ? vistaDeAdjunto(attachment) : null,
      timestamp: new Date().toISOString(),
    })

    if (mode === 'comando') {
      await handleComando(text)
      setSending(false)
      textareaRef.current?.focus()
      return
    }

    if (mode === 'imagen') {
      await handleImagen(text)
      setSending(false)
      textareaRef.current?.focus()
      return
    }

    // Modo chat
    try {
      const chatBody = { message: text, facet: activeFacet, origin: 'web' }
      if (attachment) chatBody.adjuntos = [cuerpoDeAdjunto(attachment)]
      const { data } = await api.post('/chat', chatBody)
      addMessage({
        id: Date.now().toString() + '_resp',
        facet: data.facet,
        content: data.aviso ? textoDeAviso(t, data.aviso) : data.response,
        timestamp: data.timestamp,
        contract_degraded: data.contract_degraded ?? false,
      })
      // El mensaje del usuario ya se armó con vistaDeAdjunto() más arriba,
      // que le dio su PROPIO object URL (adjuntos.js) -- el del compositor
      // ya no lo necesita nadie.
      descartarAdjuntoComposer()
    } catch (err) {
      agregarError(activeFacet, Date.now().toString() + '_err', 'errorPrefix', textoDeErrorDeMesa(t, err, t.errorFacet))
      // El id ya no existe (venció o lo borraron): no hay nada para
      // reintentar con ÉL, así que se limpia para poder re-adjuntar.
      if (codigoDe(err) === 'adjunto_no_encontrado') descartarAdjuntoComposer()
    } finally {
      setSending(false)
      textareaRef.current?.focus()
    }
  }

  async function handleComando(text) {
    const msgId = `cmd-placeholder-${Date.now()}`
    // capturado ANTES del POST: si la sesión cambia mientras está en vuelo
    // (logout+login en el mismo browser), registerPendingCommand no debe
    // registrar este taskId bajo la sesión nueva.
    const sessionEpoch = useJaxStore.getState()._sessionEpoch
    addMessage({
      id: msgId,
      facet: 'hyde',
      content: t.taskInitializing,
      status: 'running',
      timestamp: new Date().toISOString(),
    })

    try {
      const { data } = await api.post('/command', { command: text, mode: 'execute' })
      const taskId = data.task_id
      const realMsgId = `cmd-${taskId}`
      updateMessage(msgId, {
        id: realMsgId,
        content: t.taskStarted(taskId.slice(0, 8)),
        status: 'running',
      })
      registerPendingCommand(taskId, sessionEpoch)
    } catch (err) {
      updateMessage(msgId, {
        content: `**${t.errorPrefix}:** ${textoDeErrorDeMesa(t, err, t.errorTask)}`,
        status: 'completed',
      })
    }
  }

  async function handleImagen(text) {
    setGeneratingImage(true)
    try {
      const { data } = await api.post('/image/generate', { prompt: text })
      addMessage({
        id: Date.now().toString() + '_img',
        facet: 'dalle',
        content: data.revised_prompt,
        image_url: data.url,
        timestamp: new Date().toISOString(),
      })
    } catch (err) {
      agregarError('dalle', Date.now().toString() + '_img_err', 'errorPrefix', textoDeErrorDeMesa(t, err, t.errorImagen))
    } finally {
      setGeneratingImage(false)
    }
  }

  // PipelineModal muestra los errores de la creación dentro de sí mismo
  // (spec 2026-09-17 §6.2): acá sólo se crea y se anuncia. Un fallo se
  // relanza para que el modal no se cierre ni el chat se ensucie.
  async function handlePipelineSubmit(pipelineBody) {
    const { data } = await api.post('/pipelines', pipelineBody)
    addMessage({
      id: Date.now().toString(),
      facet: 'user',
      content: pipelineObjective,
      timestamp: new Date().toISOString(),
    })
    const pid = (data.pipeline_id || '').slice(0, 12)
    addMessage({
      id: `pipeline-${data.pipeline_id || Date.now()}`,
      facet: 'jacobs',
      content: t.pipelineStarted(pid, pipelineBody.mode, pipelineBody.steps?.length || 'auto'),
      timestamp: new Date().toISOString(),
    })
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <>
      {showPipelineModal && (
        <PipelineModal
          objective={pipelineObjective}
          onClose={() => setShowPipelineModal(false)}
          onSubmit={handlePipelineSubmit}
        />
      )}

      <div className="flex-shrink-0 border-t border-borde bg-fondo px-4 py-3">
        {/* Fila 1: modos, en su propia fila arriba de la caja (pedido de
            Fernando 2026-09-22) -- antes compartía fila con el textarea y le
            robaba ancho al crecer. */}
        <div data-testid="fila-modos" className="flex gap-1 mb-2">
          {MODES.map(({ id: m, label }) => (
            <button
              key={m}
              onClick={() => elegirModo(m)}
              className={`px-2 py-1 rounded text-xs font-semibold transition-colors ${
                mode === m
                  ? m === 'comando'
                    ? 'bg-modo-comando text-sobre-color'
                    : m === 'pipeline'
                    ? 'bg-texto-fuerte text-fondo'
                    : m === 'imagen'
                    ? 'bg-acento text-sobre-color'
                    : m === 'ejecutor'
                    ? 'bg-modo-ejecutor text-sobre-color'
                    : 'bg-accion text-sobre-color'
                  : 'bg-superficie text-texto-suave hover:text-texto'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Selector de faceta — solo visible en modo chat */}
        {mode === 'chat' && (
          <div className="flex gap-1 mb-2 flex-wrap">
            {FACETS.map((f) => (
              <button
                key={f.id}
                onClick={() => setActiveFacet(f.id)}
                className={`${TAMANO_BOTON_ACCION} rounded border bg-superficie font-semibold transition-colors ${
                  activeFacet === f.id
                    ? ''
                    : 'border-transparent text-texto-suave hover:text-texto'
                }`}
                style={activeFacet === f.id ? { borderColor: colorToken(f.token), color: colorToken(f.token) } : {}}
              >
                {f.label}
              </button>
            ))}
          </div>
        )}

        {/* Hint de modo */}
        {mode === 'comando' && (
          <div className="mb-2 text-xs text-aviso font-semibold flex items-center gap-1">
            <span>⚡</span>
            <span>{t.hydeHint}</span>
          </div>
        )}
        {mode === 'pipeline' && (
          <div className="mb-2 text-xs text-texto-fuerte font-semibold flex items-center gap-1">
            <span>⚙</span>
            <span>{t.jacobsHint}</span>
          </div>
        )}
        {mode === 'imagen' && (
          <div className="mb-2 text-xs text-faceta-imagen font-semibold flex items-center gap-1">
            <span>🎨</span>
            <span>{t.imagenHint}</span>
          </div>
        )}

        {mode === 'ejecutor' && (
          <div className="mb-2 text-xs text-texto-fuerte font-semibold flex items-center gap-1">
            <span>▶</span>
            <span>{t.ejecutor.hint}</span>
          </div>
        )}

        {/* File attachment preview */}
        {mode === 'chat' && (attachment || uploading) && (
          <FileAttachment
            attachment={attachment}
            uploading={uploading}
            onRemove={descartarAdjuntoComposer}
          />
        )}

        {imagenSinSoporte && (
          <div role="status" className="mb-2 text-xs text-aviso font-semibold">
            {t.adjuntoImagenSinSoporte(activeFacetObj.label)}
          </div>
        )}

        <div data-testid="fila-caja" className="flex items-end gap-3">
          {/* Attach button — sin adjuntos en el modo Ejecutor */}
          {mode !== 'ejecutor' && (
            <AttachButton
              onFileSelected={handleFileSelected}
              disabled={sending || uploading || mode !== 'chat' || !politica?.accept}
              accept={politica?.accept?.join(',')}
              title={politicaFallo ? t.adjuntoPoliticaNoDisponible : t.attachTooltip}
            />
          )}

          {/* Input */}
          <textarea
            ref={textareaRef}
            data-foco-inicial
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={1}
            placeholder={placeholder}
            disabled={sending}
            className="flex-1 bg-superficie border border-borde-control rounded-lg px-3 py-2 text-sm text-texto placeholder-texto-tenue resize-none focus:outline-none focus:border-foco disabled:opacity-50"
            style={{
              minHeight: '38px',
              borderColor: mode === 'comando' ? colorToken('modo-comando', 0.5)
                : mode === 'pipeline' ? colorToken('texto-fuerte', 0.25)
                : mode === 'imagen' ? colorToken('faceta-imagen', 0.5)
                : mode === 'ejecutor' ? colorToken('modo-ejecutor', 0.5)
                : sending ? colorToken(activeFacetObj.token, 0.5) : undefined,
            }}
          />

          {/* Send. En chat: superficie con borde y texto de la faceta activa
              (Ruling 30, decisión de Fernando): ningún texto va sobre un
              fondo sólido del color de la faceta. Mientras envía está
              disabled, así que disabled:opacity-40 hace de estado "enviando". */}
          <button
            onClick={handleSend}
            disabled={!input.trim() || sending || imagenSinSoporte}
            className={`flex-shrink-0 px-4 py-2 rounded-lg border disabled:opacity-40 text-sm font-semibold transition-colors ${
              mode === 'comando' ? 'border-transparent bg-modo-comando text-sobre-color'
                : mode === 'pipeline' ? 'border-transparent bg-texto-fuerte text-fondo'
                : mode === 'imagen' ? 'border-transparent bg-acento text-sobre-color'
                : mode === 'ejecutor' ? 'border-transparent bg-modo-ejecutor text-sobre-color'
                : 'bg-superficie'
            }`}
            style={['comando', 'pipeline', 'imagen', 'ejecutor'].includes(mode) ? undefined : {
              borderColor: colorToken(activeFacetObj.token),
              color: colorToken(activeFacetObj.token),
            }}
          >
            {mode === 'pipeline' ? t.configure : mode === 'imagen' && sending ? t.generatingImage : sending ? '…' : t.send}
          </button>

          {/* Kill Switch */}
          <KillSwitch />
        </div>
      </div>
    </>
  )
}

export default memo(BottomBar)
