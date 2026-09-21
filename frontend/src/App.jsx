import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useJaxStore } from './store/useJaxStore'
import { sincronizarApariencia } from './apariencia/sincronizarApariencia'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Historial from './pages/Historial'
import Admin from './pages/Admin'
import ResetPassword from './pages/ResetPassword'
import RequireAuth from './components/RequireAuth'

function RequireSuperadmin({ children }) {
  const user = useJaxStore((s) => s.user)
  if (!user || user.role !== 'superadmin') return <Navigate to="/" replace />
  return children
}

export default function App() {
  const restoreSession = useJaxStore((s) => s.restoreSession)

  useEffect(() => {
    restoreSession()
  }, [restoreSession])

  useEffect(() => {
    // Una vez por carga, también en Login y Reset (no hay sesión). Si falla,
    // se quedan el tema, el idioma y el nombre últimos conocidos: es
    // presentación, no autorización (spec 2026-09-14 §5.2; frente C).
    sincronizarApariencia().catch(() => {})
  }, [])

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route
          path="/"
          element={
            <RequireAuth>
              <Dashboard />
            </RequireAuth>
          }
        />
        {/* Task 9 (2026-09-18): ruta propia, hermana de "/" -- Dashboard es
            un layout fijo de 3 paneles sin sub-rutas (a diferencia de Admin,
            que sí las tiene), así que anidar acá habría pedido reestructurar
            Dashboard.jsx sólo para esta pantalla. Cualquier usuario logueado
            entra, no sólo superadmin. */}
        {/* Ronda de arreglo 1 (2026-09-18): /historial/:pipelineId es una
            ruta de verdad -- los avisos de fin de pipeline (correo y
            Telegram, jax-platform/backend/aviso_pipeline.py:175 y
            jax/jacobs/aviso.py:80) arman el enlace como
            {origen}/historial/{pipeline_id} y esperan que ABRA ese pipeline,
            no que caiga en la lista sin decir cuál era. Misma pantalla
            (Historial.jsx lee :pipelineId con useParams) para las dos rutas:
            sin id, sólo la lista; con id, la lista + el detalle abierto. */}
        <Route
          path="/historial"
          element={
            <RequireAuth>
              <Historial />
            </RequireAuth>
          }
        />
        <Route
          path="/historial/:pipelineId"
          element={
            <RequireAuth>
              <Historial />
            </RequireAuth>
          }
        />
        <Route
          path="/admin/*"
          element={
            <RequireAuth>
              <RequireSuperadmin>
                <Admin />
              </RequireSuperadmin>
            </RequireAuth>
          }
        />
        {/* Corrección (2026-09-20): Memoria vivía acá, hermana de /admin/*,
            SIN el caparazón de administración (AdminSidebar) -- de ahí las
            dos quejas de Fernando: "Volver a Axioma" mandaba al inicio (la
            única salida posible sin sidebar) y el ítem no se podía ordenar
            con los demás. Ahora es una ruta anidada más de Admin.jsx
            (Route path="memoria"), igual que dashboard/keys/users/etc.
            El candado sigue siendo el mismo: /admin/* ya está envuelto en
            RequireAuth + RequireSuperadmin, así que /admin/memoria hereda
            exactamente la misma guardia que tenía acá (superadmin-only,
            spec §1 / backend/api/admin/memoria.py::require_superadmin) --
            mover la ruta no debilita el permiso.
            Redirección de la URL vieja: la pantalla ya estuvo en producción
            en /memoria y puede haber un enlace guardado -- que no muera con
            un 404 mudo. */}
        <Route path="/memoria" element={<Navigate to="/admin/memoria" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
