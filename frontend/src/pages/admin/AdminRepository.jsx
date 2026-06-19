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
      <h1 className="text-xl font-bold text-slate-100 mb-6">{t.adminRepoTitle}</h1>

      <div className="flex gap-2 mb-4">
        {Object.keys(FOLDER_LABELS).map(f => (
          <button
            key={f}
            onClick={() => setActiveFolder(f)}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
              activeFolder === f
                ? 'bg-purple-600 text-white'
                : 'bg-slate-800 text-slate-400 hover:text-slate-200'
            }`}
          >
            {t[FOLDER_LABELS[f]]}
          </button>
        ))}
      </div>

      <div className="rounded-lg border border-slate-800 overflow-hidden">
        {files.length === 0 ? (
          <div className="px-4 py-8 text-center text-slate-600 text-sm">{t.adminRepoEmpty}</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-900 border-b border-slate-800">
              <tr>
                {['Nombre', 'Tamaño', 'Modificado', ''].map(h => (
                  <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/50">
              {files.map(f => (
                <tr key={f.path} className="bg-slate-900/50 hover:bg-slate-800/30 transition-colors">
                  <td className="px-4 py-3 text-slate-200 font-mono text-xs">{f.name}</td>
                  <td className="px-4 py-3 text-slate-500 text-xs">{t.adminRepoSize(f.size)}</td>
                  <td className="px-4 py-3 text-slate-500 text-xs">{new Date(f.modified).toLocaleString('es-HN')}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <button onClick={() => handlePreview(f)} className="text-xs px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-300 transition-colors">{t.adminRepoPreview}</button>
                      <button onClick={() => handleDownload(f)} className="text-xs px-2 py-0.5 rounded bg-blue-900/40 hover:bg-blue-900/60 text-blue-400 transition-colors">{t.adminRepoDownload}</button>
                      <button onClick={() => handleDelete(f)} className="text-xs px-2 py-0.5 rounded bg-red-900/40 hover:bg-red-900/60 text-red-400 transition-colors">{t.adminRepoDelete}</button>
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
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-6">
          <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-3xl max-h-[80vh] overflow-hidden flex flex-col shadow-2xl">
            <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800">
              <span className="text-sm font-semibold text-slate-200">{preview.filename}</span>
              <button onClick={() => setPreview(null)} className="text-slate-500 hover:text-slate-200 text-lg font-bold">×</button>
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              {preview.type === 'image' ? (
                <img src={preview.base64} alt={preview.filename} className="max-w-full rounded" />
              ) : preview.type === 'markdown' ? (
                <div className="prose prose-invert prose-sm max-w-none">
                  <ReactMarkdown>{preview.content}</ReactMarkdown>
                </div>
              ) : (
                <pre className="text-xs text-slate-300 whitespace-pre-wrap font-mono">{preview.content}</pre>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
