import { Routes, Route, Navigate } from 'react-router-dom'
import { useI18n } from '../i18n/index.jsx'
import AdminSidebar from '../components/admin/AdminSidebar'
import AdminDashboard from './admin/AdminDashboard'
import AdminApiKeys from './admin/AdminApiKeys'
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
          <Route path="keys" element={<AdminApiKeys />} />
          <Route path="users" element={<AdminUsers />} />
          <Route path="repo" element={<AdminRepository />} />
          <Route path="settings" element={<AdminSettings />} />
          <Route path="costs" element={<AdminCosts />} />
        </Routes>
      </main>
    </div>
  )
}
