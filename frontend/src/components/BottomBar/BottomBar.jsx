import { memo, useState, useRef } from 'react'
import { useJaxStore } from '../../store/useJaxStore'
import { useI18n } from '../../i18n/index.jsx'
import KillSwitch from './KillSwitch'
import PipelineModal from './PipelineModal'
import AttachButton from '../chat/AttachButton'
import FileAttachment from '../chat/FileAttachment'
import api from '../../api/client'

// Solo orden de despliegue — label/color vienen de /api/facets (tabla
// `facet`, Bloque C) via el store, no se duplican aca.
const FACET_ORDER = ['jax_local', 'jekyll', 'hipatia', 'thot', 'kimi', 'hyde', 'ada']

function BottomBar() {
  const facetsState = useJaxStore((s) => s.facets)
  const FACETS = FACET_ORDER.map((id) => ({
    id,
    label: facetsState[id]?.display_name || facetsState[id]?.name || id,
    color: facetsState[id]?.color || '#94a3b8',
  }))
  const [input, setInput] = useState('')
  const [mode, setMode] = useState('chat')
  const [sending, setSending] = useState(false)
  const [showPipelineModal, setShowPipelineModal] = useState(false)
  const [pipelineObjective, setPipelineObjective] = useState('')
  const [attachment, setAttachment] = useState(null)
  const [uploading, setUploading] = useState(false)
  const addMessage = useJaxStore((s) => s.addMessage)
  const updateMessage = useJaxStore((s) => s.updateMessage)
  const activeFacet = useJaxStore((s) => s.activeFacet)
  const setActiveFacet = useJaxStore((s) => s.setActiveFacet)
  const addToast = useJaxStore((s) => s.addToast)
  const registerPendingCommand = useJaxStore((s) => s.registerPendingCommand)
  const setGeneratingImage = useJaxStore((s) => s.setGeneratingImage)
  const { t } = useI18n()
  const textareaRef = useRef(null)

  const MODES = [
    { id: 'chat',     label: t.modeChat },
    { id: 'comando',  label: t.modeComando },
    { id: 'pipeline', label: t.modePipeline },
    { id: 'imagen',   label: t.modeImagen },
  ]

  const PLACEHOLDERS = {
    chat:     (label) => t.placeholderChat(label),
    comando:  () => t.placeholderComando(),
    pipeline: () => t.placeholderPipeline(),
    imagen:   () => t.placeholderImagen(),
  }

  const activeFacetObj = FACETS.find((f) => f.id === activeFacet) || FACETS[0]
  const placeholder = PLACEHOLDERS[mode]?.(activeFacetObj.label) || ''

  async function handleFileSelected(file) {
    setUploading(true)
    const formData = new FormData()
    formData.append('file', file)
    try {
      const { data } = await api.post('/chat/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setAttachment(data)
    } catch {
      addToast({ message: t.attachError, type: 'error' })
    } finally {
      setUploading(false)
    }
  }

  async function handleSend() {
    const text = input.trim()
    if (!text || sending) return

    if (mode === 'pipeline') {
      setPipelineObjective(text)
      setInput('')
      setShowPipelineModal(true)
      return
    }

    setSending(true)
    setInput('')

    addMessage({
      id: Date.now().toString(),
      facet: 'user',
      content: text,
      attachment: attachment ? { ...attachment } : null,
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
      const chatBody = { message: text, facet: activeFacet }
      if (attachment) {
        if (attachment.type === 'image') {
          chatBody.image_base64 = attachment.base64
          chatBody.image_filename = attachment.filename
        } else {
          chatBody.file_context = `[Archivo adjunto: ${attachment.filename}]\n\n${attachment.content || ''}`
        }
      }
      const { data } = await api.post('/chat', chatBody)
      addMessage({
        id: Date.now().toString() + '_resp',
        facet: data.facet,
        content: data.response,
        timestamp: data.timestamp,
      })
      setAttachment(null)
    } catch (err) {
      const detail = err.response?.data?.detail || t.errorFacet
      addMessage({
        id: Date.now().toString() + '_err',
        facet: activeFacet,
        content: `**Error:** ${detail}`,
        timestamp: new Date().toISOString(),
      })
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
      const detail = err.response?.data?.detail || t.errorTask
      updateMessage(msgId, {
        content: `**Error:** ${detail}`,
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
      const detail = err.response?.data?.detail || t.errorImagen
      addMessage({
        id: Date.now().toString() + '_img_err',
        facet: 'dalle',
        content: `**Error:** ${detail}`,
        timestamp: new Date().toISOString(),
      })
    } finally {
      setGeneratingImage(false)
    }
  }

  async function handlePipelineSubmit(pipelineBody) {
    addMessage({
      id: Date.now().toString(),
      facet: 'user',
      content: pipelineObjective,
      timestamp: new Date().toISOString(),
    })
    try {
      const { data } = await api.post('/pipelines', pipelineBody)
      const pid = (data.pipeline_id || '').slice(0, 12)
      addMessage({
        id: `pipeline-${data.pipeline_id || Date.now()}`,
        facet: 'jacobs',
        content: t.pipelineStarted(pid, pipelineBody.mode, pipelineBody.steps?.length || 'auto'),
        timestamp: new Date().toISOString(),
      })
    } catch (err) {
      const detail = err.response?.data?.detail || t.errorPipeline
      addMessage({
        id: `pipeline-err-${Date.now()}`,
        facet: 'jacobs',
        content: `**Error pipeline:** ${detail}`,
        timestamp: new Date().toISOString(),
      })
    }
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

      <div className="flex-shrink-0 border-t border-slate-700 bg-slate-900 px-4 py-3">
        {/* Selector de faceta — solo visible en modo chat */}
        {mode === 'chat' && (
          <div className="flex gap-1 mb-2 flex-wrap">
            {FACETS.map((f) => (
              <button
                key={f.id}
                onClick={() => setActiveFacet(f.id)}
                className={`px-2 py-0.5 rounded text-xs font-semibold transition-colors ${
                  activeFacet === f.id
                    ? 'text-slate-900'
                    : 'bg-slate-800 text-slate-400 hover:text-slate-200'
                }`}
                style={activeFacet === f.id ? { backgroundColor: f.color } : {}}
              >
                {f.label}
              </button>
            ))}
          </div>
        )}

        {/* Hint de modo */}
        {mode === 'comando' && (
          <div className="mb-2 text-xs text-orange-400 font-semibold flex items-center gap-1">
            <span>⚡</span>
            <span>{t.hydeHint}</span>
          </div>
        )}
        {mode === 'pipeline' && (
          <div className="mb-2 text-xs text-white font-semibold flex items-center gap-1">
            <span>⚙</span>
            <span>{t.jacobsHint}</span>
          </div>
        )}
        {mode === 'imagen' && (
          <div className="mb-2 text-xs font-semibold flex items-center gap-1" style={{ color: '#7c3aed' }}>
            <span>🎨</span>
            <span>{t.imagenHint}</span>
          </div>
        )}

        {/* File attachment preview */}
        {(attachment || uploading) && (
          <FileAttachment
            attachment={attachment}
            uploading={uploading}
            onRemove={() => setAttachment(null)}
          />
        )}

        <div className="flex items-end gap-3">
          {/* Mode selector */}
          <div className="flex gap-1 flex-shrink-0">
            {MODES.map(({ id: m, label }) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={`px-2 py-1 rounded text-xs font-semibold transition-colors ${
                  mode === m
                    ? m === 'comando'
                      ? 'bg-orange-600 text-white'
                      : m === 'pipeline'
                      ? 'bg-white text-slate-900'
                      : m === 'imagen'
                      ? 'text-white'
                      : 'bg-blue-600 text-white'
                    : 'bg-slate-800 text-slate-400 hover:text-slate-200'
                }`}
                style={mode === m && m === 'imagen' ? { backgroundColor: '#7c3aed' } : {}}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Attach button */}
          <AttachButton
            onFileSelected={handleFileSelected}
            disabled={sending || uploading}
          />

          {/* Input */}
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={1}
            placeholder={placeholder}
            disabled={sending}
            className="flex-1 bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 resize-none focus:outline-none focus:border-blue-500 disabled:opacity-50"
            style={{
              maxHeight: '120px',
              minHeight: '38px',
              borderColor: mode === 'comando' ? '#f97316' + '80'
                : mode === 'pipeline' ? '#ffffff40'
                : mode === 'imagen' ? '#7c3aed80'
                : sending ? activeFacetObj.color + '80' : undefined,
            }}
          />

          {/* Send */}
          <button
            onClick={handleSend}
            disabled={!input.trim() || sending}
            className="flex-shrink-0 px-4 py-2 rounded-lg disabled:opacity-40 text-white text-sm font-semibold transition-colors"
            style={{
              backgroundColor: mode === 'comando' ? '#f97316'
                : mode === 'pipeline' ? '#ffffff'
                : mode === 'imagen' ? '#7c3aed'
                : sending ? activeFacetObj.color + 'aa' : activeFacetObj.color,
              color: mode === 'pipeline' ? '#0f172a' : 'white',
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
