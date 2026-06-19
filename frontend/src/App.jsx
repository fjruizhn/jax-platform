import { useEffect } from 'react'
import { useJaxStore } from './store/useJaxStore'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'

export default function App() {
  const { token, restoreSession } = useJaxStore()

  useEffect(() => {
    restoreSession()
  }, [restoreSession])

  return token ? <Dashboard /> : <Login />
}
