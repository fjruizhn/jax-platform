import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'

const ROLES = ['superadmin', 'operator', 'viewer']

export default function AdminUsers() {
  const { t } = useI18n()
  const [users, setUsers] = useState([])
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({ email: '', role: 'operator', password: '' })
  const [saving, setSaving] = useState(false)

  function load() {
    api.get('/admin/users').then(r => setUsers(r.data.users)).catch(() => {})
  }

  useEffect(() => { load() }, [])

  async function handleCreate(e) {
    e.preventDefault()
    setSaving(true)
    try {
      await api.post('/admin/users', form)
      setShowCreate(false)
      setForm({ email: '', role: 'operator', password: '' })
      load()
    } catch {
    } finally {
      setSaving(false)
    }
  }

  async function handleStatusToggle(u) {
    const newStatus = u.status === 'active' ? 'inactive' : 'active'
    await api.put(`/admin/users/${u.user_id}`, { status: newStatus }).catch(() => {})
    load()
  }

  async function handleUnlock(u) {
    await api.post(`/admin/users/${u.user_id}/unlock`).catch(() => {})
    load()
  }

  async function handleDelete(u) {
    if (!window.confirm(t.adminDeleteConfirm(u.email))) return
    await api.delete(`/admin/users/${u.user_id}`).catch(() => {})
    load()
  }

  async function handleRoleChange(u, role) {
    await api.put(`/admin/users/${u.user_id}`, { role }).catch(() => {})
    load()
  }

  function statusBadge(u) {
    if (u.is_locked) return <span className="text-xs font-semibold text-orange-400">{t.adminUserLocked}</span>
    if (u.status === 'active') return <span className="text-xs font-semibold text-green-400">{t.adminUserActive}</span>
    return <span className="text-xs font-semibold text-slate-500">{t.adminUserInactive}</span>
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold text-slate-100">{t.adminUsersTitle}</h1>
        <button
          onClick={() => setShowCreate(true)}
          className="px-3 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-700 text-white text-sm font-semibold transition-colors"
        >
          + {t.adminUserCreate}
        </button>
      </div>

      <div className="rounded-lg border border-slate-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 border-b border-slate-800">
            <tr>
              {[t.adminUserEmail, t.adminUserRole, t.adminUserStatus, t.adminUserLastLogin, t.adminUserActions].map(h => (
                <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-slate-400 uppercase tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/50">
            {users.map(u => (
              <tr key={u.user_id} className={`hover:bg-slate-800/30 transition-colors ${u.is_locked ? 'bg-orange-950/10' : 'bg-slate-900/50'}`}>
                <td className="px-4 py-3 text-slate-200">{u.email}</td>
                <td className="px-4 py-3">
                  <select
                    value={u.role}
                    onChange={e => handleRoleChange(u, e.target.value)}
                    className="bg-slate-800 border border-slate-700 rounded px-2 py-0.5 text-xs text-slate-300 focus:outline-none"
                  >
                    {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                  </select>
                </td>
                <td className="px-4 py-3">
                  {statusBadge(u)}
                  {u.failed_attempts > 0 && !u.is_locked && (
                    <span className="ml-2 text-xs text-slate-600">({u.failed_attempts} intentos)</span>
                  )}
                </td>
                <td className="px-4 py-3 text-xs text-slate-500">
                  {u.last_login ? new Date(u.last_login).toLocaleString('es-HN') : '—'}
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2 flex-wrap">
                    {u.is_locked && (
                      <button
                        onClick={() => handleUnlock(u)}
                        className="text-xs px-2 py-0.5 rounded bg-orange-900/40 hover:bg-orange-900/60 text-orange-400 transition-colors"
                      >
                        {t.adminUserUnlock}
                      </button>
                    )}
                    <button
                      onClick={() => handleStatusToggle(u)}
                      className="text-xs px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-300 transition-colors"
                    >
                      {u.status === 'active' ? t.adminUserDisable : t.adminUserEnable}
                    </button>
                    {u.user_id !== 1 && (
                      <button
                        onClick={() => handleDelete(u)}
                        className="text-xs px-2 py-0.5 rounded bg-red-900/40 hover:bg-red-900/60 text-red-400 transition-colors"
                      >
                        {t.adminUserDelete}
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showCreate && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 w-full max-w-md shadow-2xl">
            <h2 className="text-sm font-semibold text-slate-200 mb-4">{t.adminCreateTitle}</h2>
            <form onSubmit={handleCreate} className="space-y-3">
              <input
                type="email"
                placeholder={t.adminUserEmail}
                value={form.email}
                onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
                required
                className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-purple-500"
              />
              <select
                value={form.role}
                onChange={e => setForm(f => ({ ...f, role: e.target.value }))}
                className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-purple-500"
              >
                {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
              <input
                type="password"
                placeholder={t.adminCreatePassword}
                value={form.password}
                onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
                required
                className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-purple-500"
              />
              <div className="flex gap-2 justify-end pt-2">
                <button type="button" onClick={() => setShowCreate(false)} className="px-3 py-1.5 rounded-lg text-sm text-slate-400 hover:text-slate-200 transition-colors">{t.adminCreateCancel}</button>
                <button type="submit" disabled={saving} className="px-4 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-700 text-white text-sm font-semibold disabled:opacity-50 transition-colors">{saving ? t.attachUploading : t.adminCreateSubmit}</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
