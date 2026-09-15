import { useState, useEffect, useRef } from 'react'
import { useI18n, localeFor } from '../../i18n/index.jsx'
import api from '../../api/client'
import { useJaxStore } from '../../store/useJaxStore'
import ConfirmacionSuma from '../../components/ConfirmacionSuma'
import ReactMarkdown from 'react-markdown'

const FOLDER_LABELS = {
  missions: 'adminRepoMissions',
  pipelines: 'adminRepoPipelines',
  documents: 'adminRepoDocuments',
  images: 'adminRepoImages',
}

export default function AdminRepository() {
  const { t, lang } = useI18n()
  const addToast = useJaxStore((s) => s.addToast)
  const [data, setData] = useState(null)
  const [preview, setPreview] = useState(null)
  const [activeFolder, setActiveFolder] = useState('documents')
  // Task 1 (2026-09-15): la confirmación de borrado pasa por ConfirmacionSuma
  // en vez de window.confirm, mismo patrón que la baja en AdminUsers.jsx --
  // borrandoRef espeja el estado de forma síncrona para que una respuesta que
  // llega tarde no cierre, ni le robe el foco, al diálogo de OTRO archivo que
  // se haya abierto mientras tanto.
  const [borrando, setBorrandoState] = useState(null)
  const borrandoRef = useRef(null)
  function fijarBorrando(f) {
    borrandoRef.current = f
    setBorrandoState(f)
  }
  function cerrarBorrandoSiEs(path) {
    if (borrandoRef.current?.path === path) {
      fijarBorrando(null)
      return true
    }
    return false
  }

  function load() {
    api.get('/admin/repo').then(r => setData(r.data.folders)).catch(() => {})
  }

  useEffect(() => { load() }, [])

  async function handlePreview(file) {
    const { data: fd } = await api.get(`/admin/repo/file?path=${encodeURIComponent(file.path)}`)
    setPreview({ ...fd, filename: file.name })
  }

  function handleDelete(file) {
    fijarBorrando(file)
  }

  async function confirmarBorrado() {
    const f = borrando
    try {
      await api.delete(`/admin/repo/file?path=${encodeURIComponent(f.path)}`)
      cerrarBorrandoSiEs(f.path)
      load()
    } catch (err) {
      addToast({ type: 'error', message: t.adminErrorGeneric })
    }
  }

  function handleDownload(file) {
    api.get(`/admin/repo/file?path=${encodeURIComponent(file.path)}`).then(r => {
      const el = document.createElement('a')
      if (r.data.base64) {
        el.href = r.data.base64
      } else {
        const blob = new Blob([r.data.content || ''], { type: 'text/plain' })
        el.href = URL.createObjectURL(blob)
      }
      el.download = file.name
      el.click()
    })
  }

  const files = data?.[activeFolder] || []

  return (
    <div>
      <h1 className="text-xl font-bold text-texto-fuerte mb-6">{t.adminRepoTitle}</h1>

      <div className="flex gap-2 mb-4">
        {Object.keys(FOLDER_LABELS).map(f => (
          <button
            key={f}
            onClick={() => setActiveFolder(f)}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
              activeFolder === f
                ? 'bg-acento text-sobre-color'
                : 'bg-superficie text-texto-suave hover:text-texto'
            }`}
          >
            {t[FOLDER_LABELS[f]]}
          </button>
        ))}
      </div>

      <div className="rounded-lg border border-borde overflow-hidden">
        {files.length === 0 ? (
          <div className="px-4 py-8 text-center text-texto-tenue text-sm">{t.adminRepoEmpty}</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-hundido border-b border-borde">
              <tr>
                {[t.adminRepoColName, t.adminRepoColSize, t.adminRepoColModified, ''].map((h, i) => (
                  <th key={i} className="text-left px-4 py-3 text-xs font-semibold text-texto-suave uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-borde/50">
              {files.map(f => (
                <tr key={f.path} className="bg-hundido hover:bg-superficie transition-colors">
                  <td className="px-4 py-3 text-texto font-mono text-xs">{f.name}</td>
                  <td className="px-4 py-3 text-texto-tenue text-xs">{t.adminRepoSize(f.size)}</td>
                  <td className="px-4 py-3 text-texto-tenue text-xs">{new Date(f.modified).toLocaleString(localeFor(lang))}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <button onClick={() => handlePreview(f)} className="text-xs px-2 py-0.5 rounded bg-superficie-2 text-texto hover:text-texto-fuerte transition-colors">{t.adminRepoPreview}</button>
                      <button onClick={() => handleDownload(f)} className="text-xs px-2 py-0.5 rounded bg-info-fondo text-info border border-transparent hover:border-info transition-colors">{t.adminRepoDownload}</button>
                      <button onClick={() => handleDelete(f)} className="text-xs px-2 py-0.5 rounded bg-peligro-fondo text-peligro border border-transparent hover:border-peligro-borde transition-colors">{t.adminRepoDelete}</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {borrando && (
        <ConfirmacionSuma
          titulo={t.adminRepoDeleteTitle(borrando.name)}
          mensaje={t.adminRepoDeleteMessage}
          textoConfirmar={t.adminRepoDelete}
          onConfirmar={confirmarBorrado}
          onCancelar={() => fijarBorrando(null)}
        />
      )}

      {/* Preview modal */}
      {preview && (
        <div className="fixed inset-0 bg-fondo/70 flex items-center justify-center z-50 p-6">
          <div className="bg-superficie border border-borde rounded-xl w-full max-w-3xl max-h-[80vh] overflow-hidden flex flex-col shadow-2xl">
            <div className="flex items-center justify-between px-4 py-3 border-b border-borde">
              <span className="text-sm font-semibold text-texto">{preview.filename}</span>
              <button onClick={() => setPreview(null)} className="text-texto-tenue hover:text-texto text-lg font-bold">×</button>
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              {preview.type === 'image' ? (
                <img src={preview.base64} alt={preview.filename} className="max-w-full rounded" />
              ) : preview.type === 'markdown' ? (
                // M-3 (revisión final PR 2, 2026-09-14): prose/prose-invert/prose-sm
                // son de @tailwindcss/typography, que no está instalado (plugins: []
                // en tailwind.config.js) -- no hacían nada, y prose-invert forzaría
                // texto claro en el tema claro si el plugin se agregara algún día.
                <div className="max-w-none text-texto">
                  <ReactMarkdown>{preview.content}</ReactMarkdown>
                </div>
              ) : (
                <pre className="text-xs text-texto whitespace-pre-wrap font-mono">{preview.content}</pre>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
