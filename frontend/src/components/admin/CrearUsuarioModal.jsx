import { useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import PasswordInput from '../PasswordInput'
import Dialogo from '../Dialogo'

// Alta de usuario (Ruling U27, review final de la etapa 4, 2026-09-15). Antes
// vivía en línea en AdminUsers.jsx sin role="dialog", sin aria-modal ni
// aria-labelledby, sin Escape y con los campos sólo con placeholder. Ahora va
// sobre Dialogo y cada campo tiene su <label>. `onCrear(form)` es del padre:
// hace el POST, avisa el error en un toast y cierra el modal si sale bien.
const ROLES = ['superadmin', 'operator', 'viewer']
const CAMPO = 'w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto placeholder-texto-tenue focus:outline-none focus:border-foco'
const ETIQUETA = 'block text-xs text-texto-suave mb-1'

export default function CrearUsuarioModal({ onCrear, onCerrar }) {
  const { t } = useI18n()
  const [form, setForm] = useState({ email: '', role: 'operator', password: '' })
  const [guardando, setGuardando] = useState(false)

  async function enviar(e) {
    e.preventDefault()
    setGuardando(true)
    try {
      await onCrear(form)
    } finally {
      setGuardando(false)
    }
  }

  return (
    <Dialogo idTitulo="crear-usuario-titulo" titulo={t.adminCreateTitle} onCerrar={onCerrar}>
      <form onSubmit={enviar} className="space-y-3">
        <div>
          <label htmlFor="crear-email" className={ETIQUETA}>{t.adminUserEmail}</label>
          <input
            id="crear-email"
            type="email"
            value={form.email}
            onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
            required
            className={CAMPO}
          />
        </div>
        <div>
          <label htmlFor="crear-rol" className={ETIQUETA}>{t.adminUserRole}</label>
          <select
            id="crear-rol"
            value={form.role}
            onChange={e => setForm(f => ({ ...f, role: e.target.value }))}
            className={CAMPO}
          >
            {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="crear-password" className={ETIQUETA}>{t.adminCreatePassword}</label>
          <PasswordInput
            id="crear-password"
            value={form.password}
            onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
            autoComplete="new-password"
            required
            className={CAMPO}
          />
        </div>
        <div className="flex gap-2 justify-end pt-2">
          <button type="button" onClick={onCerrar} className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors">{t.adminCreateCancel}</button>
          <button type="submit" disabled={guardando} className="px-4 py-1.5 rounded-lg bg-acento hover:bg-acento-hover text-sobre-color text-sm font-semibold disabled:opacity-50 transition-colors">{guardando ? t.attachUploading : t.adminCreateSubmit}</button>
        </div>
      </form>
    </Dialogo>
  )
}
