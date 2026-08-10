import { Routes, Route, Navigate } from 'react-router-dom'
import { useI18n } from '../i18n/index.jsx'
import AdminSidebar from '../components/admin/AdminSidebar'
import AdminDashboard from './admin/AdminDashboard'
import AdminFacetsModels from './admin/AdminFacetsModels'
import AdminUsers from './admin/AdminUsers'
import AdminRepository from './admin/AdminRepository'
import AdminSettings from './admin/AdminSettings'
import AdminCosts from './admin/AdminCosts'

export default function Admin() {
  const { t } = useI18n()

  return (
    <div className="flex h-screen bg-slate-950 text-slate-200">
      <AdminSidebar />
      <main className="flex-1 overflow-y-auto p-6">
        <Routes>
          <Route path="/" element={<Navigate to="dashboard" replace />} />
          <Route path="dashboard" element={<AdminDashboard />} />
          {/* ruta "keys" preservada a proposito (bookmarks/enlaces guardados) */}
          <Route path="keys" element={<AdminFacetsModels />} />
          <Route path="users" element={<AdminUsers />} />
          <Route path="repo" element={<AdminRepository />} />
          <Route path="settings" element={<AdminSettings />} />
          <Route path="costs" element={<AdminCosts />} />
        </Routes>
      </main>
    </div>
  )
}
