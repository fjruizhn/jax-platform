import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useJaxStore } from './store/useJaxStore'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Admin from './pages/Admin'
import ResetPassword from './pages/ResetPassword'

function RequireAuth({ children }) {
  const { token, sessionRestoring } = useJaxStore()
  if (sessionRestoring) return null
  if (!token) return <Navigate to="/login" replace />
  return children
}

function RequireSuperadmin({ children }) {
  const { user } = useJaxStore()
  if (!user || user.role !== 'superadmin') return <Navigate to="/" replace />
  return children
}

export default function App() {
  const { restoreSession } = useJaxStore()

  useEffect(() => {
    restoreSession()
  }, [restoreSession])

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
