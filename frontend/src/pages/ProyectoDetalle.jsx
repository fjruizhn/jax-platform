import { useI18n } from '../i18n/index.jsx'

// MARCADOR (E1, T7): componente mínimo para que la ruta /proyectos/:id exista.
// La Tarea 8 lo REEMPLAZA por el detalle real (miembros, ajustes, estado).
// Cuando llegue, `Number(useParams().id)` antes de llamar a la API.
export default function ProyectoDetalle() {
  const { t } = useI18n()
  return (
    <div className="min-h-dvh bg-fondo text-texto p-6">
      <h1 className="text-xl font-bold text-texto-fuerte">{t.proyectos.titulo}</h1>
    </div>
  )
}
