import { cloneElement, useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate, Outlet, useLocation } from 'react-router-dom'
import { useJaxStore } from './store/useJaxStore'
import { sincronizarApariencia } from './apariencia/sincronizarApariencia'
import { esInstalacionDeOficina } from './lib/instalacionOficina'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Historial from './pages/Historial'
import Proyectos from './pages/Proyectos'
import ProyectoDetalle from './pages/ProyectoDetalle'
import Admin from './pages/Admin'
import ResetPassword from './pages/ResetPassword'
import AccountSecurity from './pages/AccountSecurity'
import VirtualOffice from './pages/VirtualOffice'
import OfficeInvitation from './pages/OfficeInvitation'
import OfficeBootstrap from './pages/OfficeBootstrap'
import OfficeUsers from './pages/OfficeUsers'
import RequireAuth from './components/RequireAuth'
import TituloDePagina from './components/TituloDePagina'
import { officeSessionApi } from './api/virtualOffice'

function RequireSuperadmin({ children }) {
  const user = useJaxStore((s) => s.user)
  if (!user || user.role !== 'superadmin') return <Navigate to="/" replace />
  return children
}

// OP 358 pieza 1 — guardia de instalación: en el dominio de la Oficina Virtual
// este build se sirve como la oficina y ella es su única cara; la raíz y las
// rutas de la Mesa de Axioma (dashboard, historial, proyectos, administración)
// redirigen a /virtual-office, y de ahí el flujo de siempre: login si no hay
// sesión, /account/security si la oficina la deniega. En la instalación de
// Axioma es transparente. Qué hosts son de la oficina lo declara el build
// (VITE_OFFICE_HOSTS, ver lib/instalacionOficina.js) — nunca hardcodeado.
function SoloAxioma() {
  if (esInstalacionDeOficina()) return <Navigate to="/virtual-office" replace />
  return <Outlet />
}

function respuestaOfficeValida(data, userId) {
  return data && typeof data === 'object'
    && data.identity?.user_id === userId
    && typeof data.identity.tenant_id === 'number'
    && typeof data.mfa?.verified === 'boolean'
    && typeof data.mfa?.recent === 'boolean'
    && typeof data.capabilities?.create_project === 'boolean'
    && typeof data.capabilities?.administer_office === 'boolean'
    && typeof data.capabilities?.administer_identity_security === 'boolean'
}

function respuestaBootstrapValida(data, userId) {
  return data && typeof data === 'object'
    && data.state === 'BOOTSTRAP_REQUIRED'
    && data.identity?.user_id === userId
    && typeof data.identity.tenant_id === 'number'
    && typeof data.identity.account_class === 'string'
    && data.identity.global_role === null
    && data.bootstrap?.method === 'CURRENT_PASSWORD'
    && data.bootstrap.target_user_id === userId
}

function RequireOfficeSession({ children }) {
  const user = useJaxStore((s) => s.user)
  const token = useJaxStore((s) => s.token)
  const epoch = useJaxStore((s) => s._sessionEpoch)
  const location = useLocation()
  const [decision, setDecision] = useState({ status: 'pending' })
  const snapshotActual = { epoch, token, userId: user?.user_id }

  useEffect(() => {
    let active = true
    const snapshot = { epoch, token, userId: user?.user_id }
    const sigueSiendoLaMismaSesion = () => {
      const current = useJaxStore.getState()
      return active && current._sessionEpoch === snapshot.epoch && current.token === snapshot.token && current.user?.user_id === snapshot.userId
    }
    setDecision({ status: 'pending', ...snapshot })
    officeSessionApi.read()
      .then(({ data }) => {
        if (!sigueSiendoLaMismaSesion()) return
        const status = respuestaOfficeValida(data, snapshot.userId)
          ? 'allowed'
          : respuestaBootstrapValida(data, snapshot.userId) ? 'bootstrap' : 'denied'
        setDecision({ status, officeSession: data, ...snapshot })
      })
      .catch(() => { if (sigueSiendoLaMismaSesion()) setDecision({ status: 'denied', ...snapshot }) })
    return () => { active = false }
  }, [epoch, token, user?.user_id])

  const decisionVigente = decision.epoch === snapshotActual.epoch && decision.token === snapshotActual.token && decision.userId === snapshotActual.userId
  if (!decisionVigente || decision.status === 'pending') return null
  if (decision.status === 'bootstrap') return <OfficeBootstrap officeSession={decision.officeSession} />
  if (decision.status !== 'allowed') return <Navigate to="/account/security" replace state={{ from: location }} />
  const invalidateOffice = () => setDecision((current) => (
    current.status === 'allowed' && current.epoch === snapshotActual.epoch && current.token === snapshotActual.token && current.userId === snapshotActual.userId
      ? { ...current, status: 'denied' }
      : current
  ))
  return cloneElement(children, { officeSession: decision.officeSession, onOfficeDenied: invalidateOffice })
}
export default function App() {
  const restoreSession = useJaxStore((s) => s.restoreSession)
  const instalacionOficina = esInstalacionDeOficina()

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
      <TituloDePagina />
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route path="/office/invitation" element={<OfficeInvitation />} />
        <Route path="/account/security" element={<RequireAuth allowOfficeAccount><AccountSecurity /></RequireAuth>} />
        <Route
          path="/virtual-office"
          element={
            <RequireAuth allowOfficeAccount>
              <RequireOfficeSession><VirtualOffice /></RequireOfficeSession>
            </RequireAuth>
          }
        />
        <Route
          path="/virtual-office/admin/usuarios"
          element={
            <RequireAuth allowOfficeAccount>
              <RequireOfficeSession><OfficeUsers /></RequireOfficeSession>
            </RequireAuth>
          }
        />
        {/* OP 358 pieza 1: ruta pathless — en la instalación de la oficina
            redirige TODO este bloque a /virtual-office; en la de Axioma lo
            sirve tal cual estaba. */}
        <Route element={<SoloAxioma />}>
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
          {/* Ronda de arreglo 1 (2026-09-18): /historial/:pipelineId es
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
          {/* E1, T7: proyectos. /proyectos/:id monta ProyectoDetalle (T8). */}
          <Route
            path="/proyectos"
            element={
              <RequireAuth>
                <Proyectos />
              </RequireAuth>
            }
          />
          <Route
            path="/proyectos/:id"
            element={
              <RequireAuth>
                <ProyectoDetalle />
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
        </Route>
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
        <Route path="*" element={<Navigate to={instalacionOficina ? '/virtual-office' : '/'} replace />} />
      </Routes>
    </BrowserRouter>
  )
}
