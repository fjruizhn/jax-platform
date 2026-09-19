import { useState, useEffect, useRef } from 'react'
import { useI18n, localeFor } from '../../i18n/index.jsx'
import api from '../../api/client'
import { useJaxStore } from '../../store/useJaxStore'
import ConfirmacionSuma from '../../components/ConfirmacionSuma'
import Dialogo from '../../components/Dialogo'
import ReactMarkdown from 'react-markdown'
import HistorialContenido from '../../components/historial/HistorialContenido'
import PanelEjecutor from '../../components/Ejecutor/PanelEjecutor'

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
  // Ronda de arreglo 2 (2026-09-18, pedido de Fernando): la pestaña
  // "Pipelines" no tiene ruta propia (no es /historial) -- la selección del
  // detalle vive en estado local de esta pantalla, no en la URL.
  const [pipelineSeleccionado, setPipelineSeleccionado] = useState(null)
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

  // K1 (fix round 1, review de la Task 1): Preview y el borrado se excluyen
  // mutuamente, mismo patrón que los abrir* de AdminUsers.jsx -- se cierra el
  // otro ANTES de abrir el propio.
  async function handlePreview(file) {
    fijarBorrando(null)
    const { data: fd } = await api.get(`/admin/repo/file?path=${encodeURIComponent(file.path)}`)
    setPreview({ ...fd, filename: file.name })
  }

  function handleDelete(file) {
    setPreview(null)
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

      {/* Ronda de arreglo 2 (2026-09-18, pedido de Fernando): "Pipelines" y
          "Misiones" estaban SIEMPRE vacías -- no hay código que escriba
          archivos en esas dos carpetas, viven en otro lado (el historial de
          corridas en la DB, GET /pipelines; las misiones del Ejecutor en su
          propio store). En vez de una carpeta muerta, cada una muestra el
          contenido real: la pestaña deja de estar vacía CON CONTENIDO, no
          con un cartel que manda a otro lado. Fuera de la tarjeta
          `rounded-lg border` de la tabla de archivos -- no es una tabla. */}
      {activeFolder === 'pipelines' ? (
        <div>
          {/* El historial es por usuario (el backend filtra por user_id Y
              tenant_id) -- esto vive bajo Administración, sin aclarar podría
              leerse como "todos los del tenant". El filtro está bien como
              está; lo que se ajusta es el texto. */}
          <p className="text-xs text-texto-tenue mb-3">{t.adminRepoPipelinesScope}</p>
          <HistorialContenido
            pipelineId={pipelineSeleccionado?.id}
            nombreSeleccionado={pipelineSeleccionado?.nombre}
            onSelect={(id, nombreSeleccionado) => setPipelineSeleccionado({ id, nombre: nombreSeleccionado })}
            onCloseDetail={() => setPipelineSeleccionado(null)}
          />
        </div>
      ) : activeFolder === 'missions' ? (
        <PanelEjecutor />
      ) : (
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
      )}

      {borrando && (
        <ConfirmacionSuma
          titulo={t.adminRepoDeleteTitle(borrando.name)}
          mensaje={t.adminRepoDeleteMessage}
          textoConfirmar={t.adminRepoDelete}
          onConfirmar={confirmarBorrado}
          onCancelar={() => fijarBorrando(null)}
        />
      )}

      {/* K1 (fix round 1): Preview iba en un <div> a mano -- sin role, sin
          aria-modal, sin inert de #root, sin trampa de foco y sin Escape.
          Ahora va sobre Dialogo, con la misma exclusión mutua que el resto
          de los diálogos (Ruling U25/U27/U28). */}
      {preview && (
        <Dialogo idTitulo="repo-preview-titulo" titulo={preview.filename} onCerrar={() => setPreview(null)} className="max-w-3xl">
          <div className="flex justify-end -mt-2 mb-2">
            <button onClick={() => setPreview(null)} aria-label={t.adminHistoryClose} className="text-texto-tenue hover:text-texto text-lg font-bold">×</button>
          </div>
          <div className="max-h-[60vh] overflow-y-auto">
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
        </Dialogo>
      )}
    </div>
  )
}
