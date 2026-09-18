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
        <Route
          path="/historial"
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
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
