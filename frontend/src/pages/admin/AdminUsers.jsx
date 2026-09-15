import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import PasswordInput from '../../components/PasswordInput'

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
    if (u.is_locked) return <span className="text-xs font-semibold text-aviso">{t.adminUserLocked}</span>
    if (u.status === 'active') return <span className="text-xs font-semibold text-exito">{t.adminUserActive}</span>
    return <span className="text-xs font-semibold text-texto-tenue">{t.adminUserInactive}</span>
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold text-texto-fuerte">{t.adminUsersTitle}</h1>
        <button
          onClick={() => setShowCreate(true)}
          className="px-3 py-1.5 rounded-lg bg-acento hover:bg-acento-hover text-sobre-color text-sm font-semibold transition-colors"
        >
          + {t.adminUserCreate}
        </button>
      </div>

      <div className="rounded-lg border border-borde overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-hundido border-b border-borde">
            <tr>
              {[t.adminUserEmail, t.adminUserRole, t.adminUserStatus, t.adminUserLastLogin, t.adminUserActions].map(h => (
                <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-texto-suave uppercase tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-borde/50">
            {users.map(u => (
              <tr key={u.user_id} className={`hover:bg-superficie transition-colors ${u.is_locked ? 'bg-aviso-fondo' : 'bg-hundido'}`}>
                <td className="px-4 py-3 text-texto">{u.email}</td>
                <td className="px-4 py-3">
                  <select
                    value={u.role}
                    onChange={e => handleRoleChange(u, e.target.value)}
                    className="bg-superficie border border-borde rounded px-2 py-0.5 text-xs text-texto focus:outline-none"
                  >
                    {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                  </select>
                </td>
                <td className="px-4 py-3">
                  {statusBadge(u)}
                  {u.failed_attempts > 0 && !u.is_locked && (
                    <span className="ml-2 text-xs text-texto-tenue">({u.failed_attempts} intentos)</span>
                  )}
                </td>
                <td className="px-4 py-3 text-xs text-texto">
                  {u.last_login ? new Date(u.last_login).toLocaleString('es-HN') : '—'}
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2 flex-wrap">
                    {u.is_locked && (
                      <button
                        onClick={() => handleUnlock(u)}
                        className="text-xs px-2 py-0.5 rounded bg-aviso-fondo hover:bg-aviso-fondo text-aviso transition-colors"
                      >
                        {t.adminUserUnlock}
                      </button>
                    )}
                    <button
                      onClick={() => handleStatusToggle(u)}
                      className="text-xs px-2 py-0.5 rounded bg-superficie-2 text-texto hover:text-texto-fuerte transition-colors"
                    >
                      {u.status === 'active' ? t.adminUserDisable : t.adminUserEnable}
                    </button>
                    {u.user_id !== 1 && (
                      <button
                        onClick={() => handleDelete(u)}
                        className="text-xs px-2 py-0.5 rounded bg-peligro-fondo hover:bg-peligro-fondo text-peligro transition-colors"
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
        <div className="fixed inset-0 bg-fondo/70 flex items-center justify-center z-50">
          <div className="bg-superficie border border-borde rounded-xl p-6 w-full max-w-md shadow-2xl">
            <h2 className="text-sm font-semibold text-texto mb-4">{t.adminCreateTitle}</h2>
            <form onSubmit={handleCreate} className="space-y-3">
              <input
                type="email"
                placeholder={t.adminUserEmail}
                value={form.email}
                onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
                required
                className="w-full bg-superficie border border-borde-control rounded-lg px-3 py-2 text-sm text-texto placeholder-texto-tenue focus:outline-none focus:border-foco"
              />
              <select
                value={form.role}
                onChange={e => setForm(f => ({ ...f, role: e.target.value }))}
                className="w-full bg-superficie border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco"
              >
                {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
              <PasswordInput
                placeholder={t.adminCreatePassword}
                value={form.password}
                onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
                required
                className="w-full bg-superficie border border-borde-control rounded-lg px-3 py-2 text-sm text-texto placeholder-texto-tenue focus:outline-none focus:border-foco"
              />
              <div className="flex gap-2 justify-end pt-2">
                <button type="button" onClick={() => setShowCreate(false)} className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors">{t.adminCreateCancel}</button>
                <button type="submit" disabled={saving} className="px-4 py-1.5 rounded-lg bg-acento hover:bg-acento-hover text-sobre-color text-sm font-semibold disabled:opacity-50 transition-colors">{saving ? t.attachUploading : t.adminCreateSubmit}</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
