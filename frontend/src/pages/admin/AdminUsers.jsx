import { useState, useEffect } from 'react'
import { useI18n, localeFor } from '../../i18n/index.jsx'
import api from '../../api/client'
import { useJaxStore } from '../../store/useJaxStore'
import PasswordInput from '../../components/PasswordInput'
import EditarUsuarioModal from '../../components/admin/EditarUsuarioModal'
import HistorialUsuario from '../../components/admin/HistorialUsuario'
import { mensajeDeError } from './erroresAdmin'

const ROLES = ['superadmin', 'operator', 'viewer']
// Botón neutro de la fila (texto/superficie-2 y texto-fuerte/superficie-2 son
// pares declarados en PARES).
const ACCION_NEUTRA = 'text-xs px-2 py-0.5 rounded bg-superficie-2 text-texto hover:text-texto-fuerte transition-colors'

// Etapa 3 (2026-09-15): cada acción muestra su error traducido en un toast
// (antes todas terminaban en `.catch(() => {})` y el admin no se enteraba).
// Rol y estado se editan en EditarUsuarioModal. Qué está permitido lo decide
// el backend (último superadmin, auto-acciones): el botón de eliminar ya no se
// esconde para user_id 1.
export default function AdminUsers() {
  const { t, lang } = useI18n()
  const addToast = useJaxStore((s) => s.addToast)
  const [users, setUsers] = useState([])
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({ email: '', role: 'operator', password: '' })
  const [saving, setSaving] = useState(false)
  const [editando, setEditando] = useState(null)
  const [historialDe, setHistorialDe] = useState(null)

  function avisarError(err) {
    addToast({ type: 'error', message: mensajeDeError(t, err) })
  }

  function avisarExito(message) {
    addToast({ type: 'success', message })
  }

  function load() {
    api.get('/admin/users').then(r => setUsers(r.data.users)).catch(avisarError)
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
    } catch (err) {
      avisarError(err)
    } finally {
      setSaving(false)
    }
  }

  async function guardarEdicion(cambios) {
    try {
      await api.put(`/admin/users/${editando.user_id}`, cambios)
      setEditando(null)
      avisarExito(t.adminUserSaved)
      load()
    } catch (err) {
      avisarError(err)
    }
  }

  async function handleUnlock(u) {
    try {
      await api.post(`/admin/users/${u.user_id}/unlock`)
      load()
    } catch (err) {
      avisarError(err)
    }
  }

  async function handleRevoke(u) {
    try {
      await api.post(`/admin/users/${u.user_id}/revoke-sessions`)
      avisarExito(t.adminSessionsRevoked(u.email))
    } catch (err) {
      avisarError(err)
    }
  }

  async function handleResetLink(u) {
    try {
      const { data } = await api.post(`/admin/users/${u.user_id}/reset-link`)
      avisarExito(t.adminResetLinkSent(data.to))
    } catch (err) {
      avisarError(err)
    }
  }

  async function handleDelete(u) {
    if (!window.confirm(t.adminDeleteConfirm(u.email))) return
    try {
      await api.delete(`/admin/users/${u.user_id}`)
      load()
    } catch (err) {
      avisarError(err)
    }
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
                {/* Etapa 3 (Ruling U6): el rol se muestra como texto y se
                    cambia en el modal Editar; la guarda M-2 del select vive
                    ahora en EditarUsuarioModal. */}
                <td className="px-4 py-3 text-xs text-texto">{u.role}</td>
                <td className="px-4 py-3">
                  {statusBadge(u)}
                  {u.failed_attempts > 0 && !u.is_locked && (
                    <span className="ml-2 text-xs text-texto-tenue">{t.adminUserFailedAttempts(u.failed_attempts)}</span>
                  )}
                </td>
                <td className="px-4 py-3 text-xs text-texto">
                  {u.last_login ? new Date(u.last_login).toLocaleString(localeFor(lang)) : '—'}
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2 flex-wrap">
                    <button onClick={() => setEditando(u)} className={ACCION_NEUTRA}>{t.adminUserEdit}</button>
                    {u.is_locked && (
                      // M-1 (revisión final PR 2, 2026-09-14): la fila bloqueada
                      // también es bg-aviso-fondo, así que un botón con el mismo
                      // fondo no se distinguía de la fila. bg-superficie sí
                      // difiere y aviso/superficie es un par declarado en PARES.
                      <button
                        onClick={() => handleUnlock(u)}
                        className="text-xs px-2 py-0.5 rounded bg-superficie text-aviso border border-aviso-borde hover:border-aviso focus:outline-none focus-visible:ring-2 focus-visible:ring-foco transition-colors"
                      >
                        {t.adminUserUnlock}
                      </button>
                    )}
                    <button onClick={() => handleRevoke(u)} className={ACCION_NEUTRA}>{t.adminUserRevokeSessions}</button>
                    <button onClick={() => handleResetLink(u)} className={ACCION_NEUTRA}>{t.adminUserSendResetLink}</button>
                    <button onClick={() => setHistorialDe(u)} className={ACCION_NEUTRA}>{t.adminUserHistory}</button>
                    <button
                      onClick={() => handleDelete(u)}
                      className="text-xs px-2 py-0.5 rounded bg-peligro-fondo border border-transparent hover:border-peligro-borde text-peligro transition-colors"
                    >
                      {t.adminUserDelete}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editando && <EditarUsuarioModal usuario={editando} onGuardar={guardarEdicion} onCerrar={() => setEditando(null)} />}
      {historialDe && <HistorialUsuario usuario={historialDe} onCerrar={() => setHistorialDe(null)} />}

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
                className="w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto placeholder-texto-tenue focus:outline-none focus:border-foco"
              />
              <select
                value={form.role}
                onChange={e => setForm(f => ({ ...f, role: e.target.value }))}
                className="w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco"
              >
                {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
              <PasswordInput
                placeholder={t.adminCreatePassword}
                value={form.password}
                onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
                required
                className="w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto placeholder-texto-tenue focus:outline-none focus:border-foco"
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
