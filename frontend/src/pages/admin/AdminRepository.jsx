import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import ReactMarkdown from 'react-markdown'

const FOLDER_LABELS = {
  missions: 'adminRepoMissions',
  pipelines: 'adminRepoPipelines',
  documents: 'adminRepoDocuments',
  images: 'adminRepoImages',
}

export default function AdminRepository() {
  const { t } = useI18n()
  const [data, setData] = useState(null)
  const [preview, setPreview] = useState(null)
  const [activeFolder, setActiveFolder] = useState('documents')

  function load() {
    api.get('/admin/repo').then(r => setData(r.data.folders)).catch(() => {})
  }

  useEffect(() => { load() }, [])

  async function handlePreview(file) {
    const { data: fd } = await api.get(`/admin/repo/file?path=${encodeURIComponent(file.path)}`)
    setPreview({ ...fd, filename: file.name })
  }

  async function handleDelete(file) {
    if (!window.confirm(t.adminRepoDeleteConfirm(file.name))) return
    await api.delete(`/admin/repo/file?path=${encodeURIComponent(file.path)}`)
    load()
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
                {['Nombre', 'Tamaño', 'Modificado', ''].map(h => (
                  <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-texto-suave uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-borde/50">
              {files.map(f => (
                <tr key={f.path} className="bg-hundido hover:bg-superficie transition-colors">
                  <td className="px-4 py-3 text-texto font-mono text-xs">{f.name}</td>
                  <td className="px-4 py-3 text-texto-tenue text-xs">{t.adminRepoSize(f.size)}</td>
                  <td className="px-4 py-3 text-texto-tenue text-xs">{new Date(f.modified).toLocaleString('es-HN')}</td>
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
                <div className="prose prose-invert prose-sm max-w-none">
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
