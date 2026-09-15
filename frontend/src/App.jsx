import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useJaxStore } from './store/useJaxStore'
import { useTema } from './store/useTema'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
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
  const sincronizarTema = useTema((s) => s.sincronizarPredeterminado)

  useEffect(() => {
    restoreSession()
  }, [restoreSession])

  useEffect(() => {
    // Una vez por carga, también en Login y Reset (no hay sesión). Si falla, se
    // queda el último predeterminado conocido que ya aplicó el script de
    // index.html: es una preferencia visual, no una autorización (spec §5.2).
    sincronizarTema().catch(() => {})
  }, [sincronizarTema])

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
