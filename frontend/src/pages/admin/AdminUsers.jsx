import { useState, useEffect, useRef } from 'react'
import { useI18n, localeFor } from '../../i18n/index.jsx'
import api from '../../api/client'
import { useJaxStore } from '../../store/useJaxStore'
import EditarUsuarioModal from '../../components/admin/EditarUsuarioModal'
import HistorialUsuario from '../../components/admin/HistorialUsuario'
import CrearUsuarioModal from '../../components/admin/CrearUsuarioModal'
import FijarPasswordModal from '../../components/admin/FijarPasswordModal'
import ConfirmacionSuma from '../../components/ConfirmacionSuma'
import { mensajeDeError } from './erroresAdmin'
import { codigoDe } from '../../api/errores'
import { TAMANO_BOTON_ACCION } from '../../tema/botones'

// Botón neutro de la fila (texto/superficie-2 y texto-fuerte/superficie-2 son
// pares declarados en PARES).
// Hallazgo de revisión, 2026-09-21: media 20px de alto (text-xs + py-0.5,
// medido) -- bajo el mínimo, misma familia que TAMANO_BOTON_ACCION.
const ACCION_NEUTRA = `${TAMANO_BOTON_ACCION} rounded bg-superficie-2 text-texto hover:text-texto-fuerte transition-colors`

// Etapa 3 (2026-09-15): cada acción muestra su error traducido en un toast
// (antes todas terminaban en `.catch(() => {})` y el admin no se enteraba).
// Rol y estado se editan en EditarUsuarioModal. Qué está permitido lo decide
// el backend (último superadmin, auto-acciones): el botón de eliminar ya no se
// esconde para user_id 1.
export default function AdminUsers() {
  const { t, lang } = useI18n()
  const addToast = useJaxStore((s) => s.addToast)
  const actualizarMiEmail = useJaxStore((s) => s.actualizarMiEmail)
  // Task 1 (2026-09-15, decisión de Fernando, revierte Ruling F7): la fila
  // del usuario logueado no ofrece "Fijar contraseña" ni "Dar de baja" -- es
  // solo la UI; el backend sigue rechazando la auto-acción con 403
  // auto_accion_prohibida como defensa en profundidad (sin tocar acá).
  const usuarioLogueado = useJaxStore((s) => s.user)
  const [users, setUsers] = useState([])
  // Task 2 (2026-09-15, DEUDA U36): historial de las bajas visible desde la
  // UI. `mostrarBajas` es el estado del interruptor (aria-pressed, mismo
  // patrón que el ojito de PasswordInput); la lista de bajas se pide recién
  // al activarlo, no en cada montaje -- es de solo lectura, no participa de
  // la exclusión mutua de los modales (Ruling U25/U28): no abre nada por sí
  // misma, sólo su botón "Historial" reusa abrirHistorial.
  const [mostrarBajas, setMostrarBajas] = useState(false)
  const [bajas, setBajas] = useState([])
  // Fix wave final (2026-09-15): una baja que termina con el interruptor
  // encendido recarga TAMBIÉN la lista de bajas. El ref espeja el
  // interruptor para leerlo cuando la respuesta llega (la baja pudo
  // empezar con él apagado). Apagado no hace falta marca de "vieja":
  // encenderlo siempre vuelve a pedir la lista.
  const mostrarBajasRef = useRef(false)
  const [showCreate, setShowCreate] = useState(false)
  const [editando, setEditando] = useState(null)
  const [historialDe, setHistorialDe] = useState(null)
  const [dandoDeBaja, setDandoDeBajaState] = useState(null)
  // dandoDeBajaRef espeja el estado de forma síncrona (m3, revisión final de
  // la etapa 5, Ruling U36): confirmarBaja necesita saber, cuando la
  // respuesta llega, si el diálogo que abrió sigue siendo el que está en
  // pantalla -- y el estado de React no se puede leer de forma síncrona
  // fuera de un render. fijarDandoDeBaja es el único punto de escritura.
  const dandoDeBajaRef = useRef(null)
  function fijarDandoDeBaja(u) {
    dandoDeBajaRef.current = u
    setDandoDeBajaState(u)
  }
  // Cierra el diálogo de baja de `userId` SOLO si sigue siendo el que está
  // abierto: una confirmación que resuelve tarde (éxito o 404) no debe
  // cerrar, ni robarle el foco, al diálogo de OTRO usuario que el admin haya
  // abierto mientras esperaba (Cancelar/Escape siguen habilitados durante el
  // pedido en vuelo). Devuelve si cerró el suyo, para que quien llama sepa si
  // también le toca mover el foco.
  function cerrarBajaSiEs(userId) {
    if (dandoDeBajaRef.current?.user_id === userId) {
      fijarDandoDeBaja(null)
      return true
    }
    return false
  }
  // Fijar contraseña (2026-09-15, decisiones de Fernando que revierten U2):
  // el mismo patrón que la baja -- estado espejado en un ref con un único
  // punto de escritura, y cerrarFijarSiEs para que un éxito que llega tarde no
  // cierre el diálogo de OTRO usuario abierto mientras tanto.
  const [fijandoPassword, setFijandoPasswordState] = useState(null)
  const fijandoPasswordRef = useRef(null)
  function fijarFijandoPassword(u) {
    fijandoPasswordRef.current = u
    setFijandoPasswordState(u)
  }
  function cerrarFijarSiEs(userId) {
    if (fijandoPasswordRef.current?.user_id === userId) fijarFijandoPassword(null)
  }
  // Ruling U35 (WCAG 2.4.3, 2026-09-15): tras una baja exitosa, Dialogo
  // devuelve el foco al botón "Dar de baja" de la fila (su cleanup de
  // useLayoutEffect al desmontarse -- ver components/Dialogo.jsx), y load()
  // borra esa fila: el foco caía a body. Se lleva a "+ Nuevo usuario" con un
  // efecto propio. Lo que hace esto seguro NO es esperar a que load()
  // termine: es que React corre todo cleanup de useLayoutEffect antes que
  // cualquier useEffect pasivo del MISMO commit, así que el cleanup síncrono
  // de Dialogo (en el commit en que este componente lo desmonta) siempre
  // termina antes de que un efecto pasivo de acá pueda correr y disputarle el
  // foco. Si Dialogo alguna vez moviera esa restauración a un useEffect
  // pasivo, esa garantía de orden desaparecería.
  const botonNuevo = useRef(null)
  const [enfocarNuevo, setEnfocarNuevo] = useState(false)
  useEffect(() => {
    if (!enfocarNuevo) return
    botonNuevo.current?.focus()
    setEnfocarNuevo(false)
  }, [enfocarNuevo])
  // Ruling U25 (fix round 2, 2026-09-15): un solo modal a la vez -- antes
  // `editando` y `historialDe` eran independientes, y el fondo seguía
  // alcanzable con Tab detrás de un modal (sin `inert` ni trampa de foco), así
  // que un par de Tabs y Enter podían abrir Historial ENCIMA de Editar, y un
  // solo Escape cerraba los dos a la vez -- descartando en silencio una
  // edición sin guardar. Los `abrir*` cierran los otros dos antes de abrir el
  // suyo. El fondo inert ya no es local (data-admin-contenido dejaba vivo el
  // AdminSidebar): Dialogo marca #root entero (Ruling U27, 2026-09-15).

  // Etapa 5 (Ruling U28): la baja (ConfirmacionSuma) entra a la misma
  // exclusión mutua. Fijar contraseña (2026-09-15) también.

  function abrirCrear() {
    setEditando(null)
    setHistorialDe(null)
    fijarFijandoPassword(null)
    fijarDandoDeBaja(null)
    setShowCreate(true)
  }

  function abrirEdicion(u) {
    setHistorialDe(null)
    setShowCreate(false)
    fijarFijandoPassword(null)
    fijarDandoDeBaja(null)
    setEditando(u)
  }

  function abrirHistorial(u) {
    setEditando(null)
    setShowCreate(false)
    fijarFijandoPassword(null)
    fijarDandoDeBaja(null)
    setHistorialDe(u)
  }

  function abrirBaja(u) {
    setEditando(null)
    setShowCreate(false)
    fijarFijandoPassword(null)
    setHistorialDe(null)
    fijarDandoDeBaja(u)
  }

  function abrirFijarPassword(u) {
    setEditando(null)
    setShowCreate(false)
    setHistorialDe(null)
    fijarDandoDeBaja(null)
    fijarFijandoPassword(u)
  }

  function avisarError(err) {
    addToast({ type: 'error', message: mensajeDeError(t, err) })
  }

  function avisarExito(message) {
    addToast({ type: 'success', message })
  }

  function load() {
    return api.get('/admin/users').then(r => setUsers(r.data.users)).catch(avisarError)
  }

  // Ronda 2 (2026-09-15): dos pedidos de bajas en vuelo pueden resolverse
  // fuera de orden. Cada pedido toma un número; sólo se aplica (lista o
  // error) el del último pedido hecho, y una respuesta vieja se descarta.
  const pedidoBajas = useRef(0)
  function cargarBajas() {
    const n = ++pedidoBajas.current
    return api.get('/admin/users?bajas=true')
      .then(r => { if (n === pedidoBajas.current) setBajas(r.data.users) })
      .catch(err => { if (n === pedidoBajas.current) avisarError(err) })
  }

  function alternarBajas() {
    const activar = !mostrarBajas
    mostrarBajasRef.current = activar
    setMostrarBajas(activar)
    if (activar) cargarBajas()
  }

  // Tras una baja (o el 404 de otra que ganó la carrera): activos siempre,
  // bajas si su lista está a la vista.
  function recargarTrasBaja() {
    return Promise.all([load(), mostrarBajasRef.current ? cargarBajas() : null])
  }

  useEffect(() => { load() }, [])

  async function crearUsuario(form) {
    try {
      await api.post('/admin/users', form)
      setShowCreate(false)
      load()
    } catch (err) {
      avisarError(err)
    }
  }

  async function guardarEdicion(cambios) {
    try {
      await api.put(`/admin/users/${editando.user_id}`, cambios)
      // Si el correo editado es el propio, la barra de usuario no queda vieja.
      if (cambios.email !== undefined) actualizarMiEmail(editando.user_id, cambios.email)
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

  // Fijar contraseña: el backend cierra las sesiones del usuario y le prende
  // la marca de cambio obligatorio. Un error deja el diálogo abierto con su
  // toast.
  async function fijarPassword(nueva) {
    const u = fijandoPassword
    try {
      await api.post(`/admin/users/${u.user_id}/password`, { new_password: nueva })
      cerrarFijarSiEs(u.user_id)
      avisarExito(t.adminPasswordSetDone(u.email))
    } catch (err) {
      avisarError(err)
    }
  }

  // Eliminar = dar de baja (etapa 5): confirmación por suma en vez del
  // confirm del navegador, y POST /baja (el backend ya no acepta el borrado:
  // 405). m2 (revisión final, Ruling U36): un 404 usuario_no_encontrado
  // significa que OTRO admin ya lo dio de baja antes de que este pedido
  // terminara -- se cierra el diálogo, se avisa con el toast traducido y se
  // recarga la lista para que la fila vieja desaparezca. Cualquier otro
  // error (409 ultimo_superadmin, la guarda de auto-acción...) deja el
  // diálogo abierto con su toast, como antes. m3: cerrarBajaSiEs sólo toca
  // el diálogo si sigue siendo el de este usuario -- ver su comentario.
  async function confirmarBaja() {
    const u = dandoDeBaja
    try {
      await api.post(`/admin/users/${u.user_id}/baja`)
      const eraLaAbierta = cerrarBajaSiEs(u.user_id)
      avisarExito(t.adminBajaDone(u.email))
      await recargarTrasBaja()
      if (eraLaAbierta) setEnfocarNuevo(true)
    } catch (err) {
      if (codigoDe(err) === 'usuario_no_encontrado') {
        cerrarBajaSiEs(u.user_id)
        avisarError(err)
        await recargarTrasBaja()
      } else {
        avisarError(err)
      }
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
        <div className="flex items-center gap-2">
          <button
            onClick={alternarBajas}
            aria-pressed={mostrarBajas}
            className={`px-3 py-1.5 rounded-lg text-sm font-semibold transition-colors ${
              mostrarBajas
                ? 'bg-acento text-sobre-color'
                : 'bg-superficie-2 text-texto hover:text-texto-fuerte'
            }`}
          >
            {t.adminUserShowBajas}
          </button>
          <button
            ref={botonNuevo}
            onClick={abrirCrear}
            className="px-3 py-1.5 rounded-lg bg-acento hover:bg-acento-hover text-sobre-color text-sm font-semibold transition-colors"
          >
            + {t.adminUserCreate}
          </button>
        </div>
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
            {users.map(u => {
              const esPropia = usuarioLogueado?.user_id === u.user_id
              return (
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
                    <button onClick={() => abrirEdicion(u)} className={ACCION_NEUTRA}>{t.adminUserEdit}</button>
                    {u.is_locked && (
                      // M-1 (revisión final PR 2, 2026-09-14): la fila bloqueada
                      // también es bg-aviso-fondo, así que un botón con el mismo
                      // fondo no se distinguía de la fila. bg-superficie sí
                      // difiere y aviso/superficie es un par declarado en PARES.
                      <button
                        onClick={() => handleUnlock(u)}
                        className={`${TAMANO_BOTON_ACCION} rounded bg-superficie text-aviso border border-aviso-borde hover:border-aviso focus:outline-none focus-visible:ring-2 focus-visible:ring-foco transition-colors`}
                      >
                        {t.adminUserUnlock}
                      </button>
                    )}
                    <button onClick={() => handleRevoke(u)} className={ACCION_NEUTRA}>{t.adminUserRevokeSessions}</button>
                    <button onClick={() => handleResetLink(u)} className={ACCION_NEUTRA}>{t.adminUserSendResetLink}</button>
                    {!esPropia && (
                      <button onClick={() => abrirFijarPassword(u)} className={ACCION_NEUTRA}>{t.adminUserSetPassword}</button>
                    )}
                    <button onClick={() => abrirHistorial(u)} className={ACCION_NEUTRA}>{t.adminUserHistory}</button>
                    {!esPropia && (
                      <button
                        onClick={() => abrirBaja(u)}
                        className={`${TAMANO_BOTON_ACCION} rounded bg-peligro-fondo border border-transparent hover:border-peligro-borde text-peligro transition-colors`}
                      >
                        {t.adminUserBaja}
                      </button>
                    )}
                  </div>
                </td>
              </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {mostrarBajas && (
        <div className="rounded-lg border border-borde overflow-hidden mt-6">
          <table className="w-full text-sm">
            <thead className="bg-hundido border-b border-borde">
              <tr>
                {[t.adminBajaListEmail, t.adminBajaListDeletedAt, t.adminBajaListDeletedBy, t.adminUserActions].map(h => (
                  <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-texto-suave uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-borde/50">
              {bajas.map(b => (
                <tr key={b.user_id} className="bg-hundido">
                  <td className="px-4 py-3 text-texto">{b.email_original}</td>
                  <td className="px-4 py-3 text-xs text-texto">
                    {b.deleted_at ? new Date(b.deleted_at).toLocaleString(localeFor(lang)) : '—'}
                  </td>
                  <td className="px-4 py-3 text-xs text-texto">{b.deleted_by_email || '—'}</td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => abrirHistorial({ user_id: b.user_id, email: b.email_original })}
                      className={ACCION_NEUTRA}
                    >
                      {t.adminUserHistory}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editando && <EditarUsuarioModal usuario={editando} onGuardar={guardarEdicion} onCerrar={() => setEditando(null)} />}
      {historialDe && <HistorialUsuario usuario={historialDe} onCerrar={() => setHistorialDe(null)} />}
      {dandoDeBaja && (
        <ConfirmacionSuma
          titulo={t.adminBajaTitle(dandoDeBaja.email)}
          mensaje={t.adminBajaMessage}
          textoConfirmar={t.adminUserBaja}
          onConfirmar={confirmarBaja}
          onCancelar={() => fijarDandoDeBaja(null)}
        />
      )}

      {fijandoPassword && (
        <FijarPasswordModal usuario={fijandoPassword} onFijar={fijarPassword} onCerrar={() => fijarFijandoPassword(null)} />
      )}

      {showCreate && <CrearUsuarioModal onCrear={crearUsuario} onCerrar={() => setShowCreate(false)} />}
    </div>
  )
}
