import { useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import { useCerrarConEscape } from '../../lib/useCerrarConEscape'

// Editar rol y estado (2026-09-15, admin usuarios etapa 3). Manda SOLO lo que
// cambió; qué está permitido lo decide el backend (último superadmin,
// auto-acciones) y el padre muestra el error traducido. `deleted` no es una
// opción: eso es la baja. Pinta solo con tokens (está en MIGRADOS).
const ROLES = ['superadmin', 'operator', 'viewer']
const ESTADOS = ['active', 'inactive']
// M-2: border-borde-control tiene su par de 3:1 sobre hundido; el foco se ve
// con focus:border-foco (focus:outline-none sin reemplazo no vale).
const CAMPO = 'w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco'
const ETIQUETA = 'block text-xs text-texto-suave mb-1'

export default function EditarUsuarioModal({ usuario, onGuardar, onCerrar }) {
  const { t } = useI18n()
  const [role, setRole] = useState(usuario.role)
  const [status, setStatus] = useState(usuario.status)
  const [guardando, setGuardando] = useState(false)
  useCerrarConEscape(onCerrar)

  const cambios = {}
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
    <div className="fixed inset-0 bg-fondo/70 flex items-center justify-center z-50">
      <div role="dialog" aria-modal="true" aria-labelledby="editar-usuario-titulo"
        className="bg-superficie border border-borde rounded-xl p-6 w-full max-w-md shadow-2xl">
        <h2 id="editar-usuario-titulo" className="text-sm font-semibold text-texto mb-4">{t.adminUserEditTitle(usuario.email)}</h2>
        <form onSubmit={enviar} className="space-y-3">
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
      </div>
    </div>
  )
}
