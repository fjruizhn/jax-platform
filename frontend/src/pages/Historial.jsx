import { Link, useNavigate, useParams, useLocation } from 'react-router-dom'
import { useI18n } from '../i18n/index.jsx'
import { useNombreDelSistema } from '../store/useApariencia'
import HistorialContenido from '../components/historial/HistorialContenido'

// Task 9 (2026-09-18, historial-y-arreglos-de-pipeline): "un lugar... donde
// se listen los pipelines que se hicieron y se pueda leer el prompt que
// generaste" -- pedido textual de Fernando. GET /api/pipelines (Task 7) ya
// trae todo lo que esta pantalla necesita; sólo faltaba dónde mostrarlo.
//
// Ronda de arreglo 1 (2026-09-18): el detalle es la URL /historial/:pipelineId
// (App.jsx tiene las dos rutas, "/historial" y "/historial/:pipelineId",
// apuntando las dos acá) -- NO estado interno. Los avisos de fin de pipeline
// por correo y Telegram (otras dos tareas de la misma ronda,
// jax-platform/backend/aviso_pipeline.py:175 y jax/jacobs/aviso.py:80) arman
// el enlace como {origen}/historial/{pipeline_id} y esperan que abra ESE
// pipeline al entrar directo por la URL -- con estado interno no hay forma
// de que un link externo abra nada.
//
// Ronda de arreglo 2 (2026-09-18): el cuerpo (lista + detalle) vive en
// HistorialContenido -- esta pantalla sólo pone la cabecera de página
// (título, "Volver a Axioma") y traduce la URL (useParams/useNavigate) a las
// props que el cuerpo entiende. Es el mismo cuerpo que se monta en la
// pestaña Repositorio → Pipelines (AdminRepository.jsx), sin ruta propia ahí
// -- pedido de Fernando: una sola implementación, dos lugares.
export default function Historial() {
  const { t } = useI18n()
  const nombre = useNombreDelSistema(t)
  const navigate = useNavigate()
  const location = useLocation()
  const { pipelineId } = useParams()

  return (
    <div className="min-h-dvh bg-fondo text-texto p-6">
      <div className="max-w-5xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-xl font-bold text-texto-fuerte">{t.historialTitle}</h1>
          <Link to="/" className="text-xs text-texto-tenue hover:text-texto transition-colors flex items-center gap-1.5">
            <span>←</span>
            <span>{t.historialBack(nombre)}</span>
          </Link>
        </div>

        <HistorialContenido
          pipelineId={pipelineId}
          nombreSeleccionado={location.state?.name}
          onSelect={(id, nombreSeleccionado) => navigate(`/historial/${id}`, { state: { name: nombreSeleccionado } })}
          onCloseDetail={() => navigate('/historial')}
        />
      </div>
    </div>
  )
}
