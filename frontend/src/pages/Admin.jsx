import { Routes, Route, Navigate } from 'react-router-dom'
import AdminSidebar from '../components/admin/AdminSidebar'
import Toast from '../components/Notifications/Toast'
import AdminDashboard from './admin/AdminDashboard'
import AdminFacetsModels from './admin/AdminFacetsModels'
import AdminUsers from './admin/AdminUsers'
import AdminRepository from './admin/AdminRepository'
import AdminSettings from './admin/AdminSettings'
import AdminCosts from './admin/AdminCosts'
import AdminSmtp from './admin/AdminSmtp'
import Memoria from './Memoria'

export default function Admin() {
  return (
    <div className="flex h-dvh bg-fondo text-texto">
      <AdminSidebar />
      {/* data-foco-inicial: RequireAuth pone el foco acá al terminar el
          cambio obligatorio de contraseña (U34). */}
      <main data-foco-inicial tabIndex={-1} className="flex-1 overflow-y-auto p-6 focus:outline-none">
        <Routes>
          <Route path="/" element={<Navigate to="dashboard" replace />} />
          <Route path="dashboard" element={<AdminDashboard />} />
          {/* ruta "keys" preservada a proposito (bookmarks/enlaces guardados) */}
          <Route path="keys" element={<AdminFacetsModels />} />
          <Route path="users" element={<AdminUsers />} />
          <Route path="repo" element={<AdminRepository />} />
          <Route path="settings" element={<AdminSettings />} />
          <Route path="costs" element={<AdminCosts />} />
          <Route path="smtp" element={<AdminSmtp />} />
          {/* Corrección (2026-09-20): Memoria vive acá, como cualquier otra
              pantalla de Administración -- antes era una ruta suelta de
              App.jsx sin sidebar (ver App.jsx y AdminSidebar.jsx). */}
          <Route path="memoria" element={<Memoria />} />
        </Routes>
      </main>
      {/* Etapa 3 (2026-09-15): los toasts solo se montaban en Dashboard; en
          Admin los errores traducidos de las acciones eran invisibles. */}
      <Toast />
    </div>
  )
}
