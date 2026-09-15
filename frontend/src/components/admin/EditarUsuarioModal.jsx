import { useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import Dialogo from '../Dialogo'

// Editar correo, rol y estado (2026-09-15, admin usuarios etapas 3 y 5). Manda
// SOLO lo que cambió (el correo, recortado); qué está permitido lo decide el
// backend (formato, único, último superadmin, auto-acciones) y el padre
// muestra el error traducido. `deleted` no es una
// opción: eso es la baja. Pinta solo con tokens. El comportamiento de diálogo
// (portal, #root inert, foco, Escape) lo pone Dialogo (Ruling U27).
const ROLES = ['superadmin', 'operator', 'viewer']
const ESTADOS = ['active', 'inactive']
// M-2: border-borde-control tiene su par de 3:1 sobre hundido; el foco se ve
// con focus:border-foco (focus:outline-none sin reemplazo no vale).
const CAMPO = 'w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco'
const ETIQUETA = 'block text-xs text-texto-suave mb-1'

export default function EditarUsuarioModal({ usuario, onGuardar, onCerrar }) {
  const { t } = useI18n()
  const [email, setEmail] = useState(usuario.email)
  const [role, setRole] = useState(usuario.role)
  const [status, setStatus] = useState(usuario.status)
  const [guardando, setGuardando] = useState(false)

  const cambios = {}
  if (email.trim() !== usuario.email) cambios.email = email.trim()
  if (role !== usuario.role) cambios.role = role
  if (status !== usuario.status) cambios.status = status
  const hayCambios = Object.keys(cambios).length > 0

  async function enviar(e) {
    e.preventDefault()
    setGuardando(true)
    try {
      await onGuardar(cambios)
    } finally {
      setGuardando(false)
    }
  }

  const etiquetaEstado = { active: t.adminUserActive, inactive: t.adminUserInactive }

  return (
    <Dialogo idTitulo="editar-usuario-titulo" titulo={t.adminUserEditTitle(usuario.email)} onCerrar={onCerrar}>
        <form onSubmit={enviar} className="space-y-3">
          <div>
            <label htmlFor="editar-email" className={ETIQUETA}>{t.adminUserEmail}</label>
            <input id="editar-email" type="email" maxLength={254} value={email} onChange={(e) => setEmail(e.target.value)} className={CAMPO} />
          </div>
          <div>
            <label htmlFor="editar-rol" className={ETIQUETA}>{t.adminUserRole}</label>
            <select id="editar-rol" value={role} onChange={(e) => setRole(e.target.value)} className={CAMPO}>
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="editar-estado" className={ETIQUETA}>{t.adminUserStatus}</label>
            <select id="editar-estado" value={status} onChange={(e) => setStatus(e.target.value)} className={CAMPO}>
              {ESTADOS.map((s) => <option key={s} value={s}>{etiquetaEstado[s]}</option>)}
            </select>
          </div>
          <div className="flex gap-2 justify-end pt-2">
            <button type="button" onClick={onCerrar} className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors">{t.adminCreateCancel}</button>
            <button type="submit" disabled={!hayCambios || guardando} className="px-4 py-1.5 rounded-lg bg-acento hover:bg-acento-hover text-sobre-color text-sm font-semibold disabled:opacity-50 transition-colors">{t.adminUserSave}</button>
          </div>
        </form>
    </Dialogo>
  )
}
