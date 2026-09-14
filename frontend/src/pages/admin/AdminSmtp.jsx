import { useState, useEffect } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import api from '../../api/client'
import PasswordInput from '../../components/PasswordInput'

// Correo saliente (2026-09-12, etapa 1 de administración de usuarios): copia
// del comportamiento de AteneaERP. La contraseña nunca vuelve del backend: si
// hay una guardada llega la máscara, y guardar con la máscara la conserva.
// Cada error es un código estable que se traduce; si el servidor SMTP
// respondió algo, se muestra además tal cual (es lo que hace falta para
// arreglarlo).
const MASCARA = '••••••••'
// Puerto estándar de cada cifrado (números de protocolo, no configuración):
// al CAMBIAR el cifrado el puerto pasa a este; después sigue editable. Al
// cargar no se toca el guardado (2026-09-13, como el ERP).
const PUERTO_POR_CIFRADO = { none: 25, tls: 587, ssl: 465 }
const VACIO = { host: '', port: 587, encryption: 'tls', user: '', password: '', from_name: '', from_email: '', test_to: '' }
const DIALOGO_CERRADO = { abierto: false, to: '', error: null }
const INPUT = 'w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-blue-500'
const LABEL = 'block text-xs text-slate-400 mb-1 font-semibold uppercase tracking-wider'
const BOTON = 'px-3 py-1.5 rounded-lg text-sm font-semibold transition-colors disabled:opacity-50'

function codigoDe(err) {
  const detail = err?.response?.data?.detail
  return typeof detail === 'string' ? detail : detail?.code
}

export default function AdminSmtp() {
  const { t } = useI18n()
  const [form, setForm] = useState(VACIO)
  const [estado, setEstado] = useState({ corrupta: false, motivo: null })
  const [ocupado, setOcupado] = useState(null)
  const [resultado, setResultado] = useState(null)
  const [emailSesion, setEmailSesion] = useState('')
  const [dialogo, setDialogo] = useState(DIALOGO_CERRADO)

  function mensaje(err) {
    const code = codigoDe(err)
    const base = (code && t.smtpErrors[code]) || t.smtpErrorGeneric
    const servidor = err?.response?.data?.detail?.server
    return servidor ? `${base} ${t.smtpServerSaid(servidor)}` : base
  }

  function motivoLegible(motivo) {
    const [tipo, clave] = (motivo || '').split(':')
    if (tipo === 'password_ilegible') return t.smtpMotivoPasswordIlegible
    if (tipo === 'clave_ausente') return t.smtpMotivoClaveAusente(clave)
    if (tipo === 'valor_invalido') return t.smtpMotivoValorInvalido(clave)
    return t.smtpErrorGeneric
  }

  async function cargar() {
    const { data } = await api.get('/admin/smtp')
    setForm({
      host: data.host, port: data.port, encryption: data.encryption, user: data.user,
      password: data.password, from_name: data.from_name, from_email: data.from_email,
      test_to: data.test_to ?? '',
    })
    setEstado({ corrupta: data.corrupta, motivo: data.motivo })
    setEmailSesion(data.email_sesion ?? '')
  }

  useEffect(() => {
    cargar().catch((err) => setResultado({ ok: false, texto: mensaje(err) }))
  }, [])

  function set(campo, valor) {
    setForm((f) => ({ ...f, [campo]: valor }))
  }

  function cambiarCifrado(cifrado) {
    setForm((f) => ({ ...f, encryption: cifrado, port: PUERTO_POR_CIFRADO[cifrado] }))
  }

  async function accion(nombre, llamada, textoOk) {
    setOcupado(nombre)
    setResultado(null)
    try {
      const respuesta = await llamada()
      setResultado({ ok: true, texto: textoOk(respuesta?.data) })
    } catch (err) {
      setResultado({ ok: false, texto: mensaje(err) })
    } finally {
      setOcupado(null)
    }
  }

  function guardar(e) {
    e.preventDefault()
    accion('guardar', async () => {
      await api.put('/admin/smtp', { ...form, port: Number(form.port) })
      // Ya se guardó: si la recarga falla, el guardado sigue siendo un éxito
      // y se avisa aparte (antes se mostraba como error).
      try {
        await cargar()
        return { data: { recargado: true } }
      } catch {
        return { data: { recargado: false } }
      }
    }, (data) => (data.recargado ? t.smtpSaved : `${t.smtpSaved} ${t.smtpReloadFailed}`))
  }

  function probarConexion() {
    const { host, port, encryption, user, password } = form
    accion('conexion', () => api.post('/admin/smtp/test-connection', { host, port: Number(port), encryption, user, password }),
      () => t.smtpConnectionOk)
  }

  // "Enviar correo de prueba" pregunta primero a quién (como el ERP):
  // prellenado con el destinatario por defecto o, sin él, con el de la sesión.
  function abrirDialogo() {
    setResultado(null)
    setDialogo({ abierto: true, to: form.test_to.trim() || emailSesion, error: null })
  }

  function cerrarDialogo() {
    if (ocupado === 'prueba') return
    setDialogo(DIALOGO_CERRADO)
  }

  async function enviarPrueba(e) {
    e.preventDefault()
    setOcupado('prueba')
    setDialogo((d) => ({ ...d, error: null }))
    try {
      const { data } = await api.post('/admin/smtp/test', { to: dialogo.to })
      setDialogo(DIALOGO_CERRADO)
      setResultado({ ok: true, texto: t.smtpTestSent(data?.to) })
    } catch (err) {
      // Dentro del diálogo: el destinatario sigue a la vista y se puede corregir.
      setDialogo((d) => ({ ...d, error: mensaje(err) }))
    } finally {
      setOcupado(null)
    }
  }

  return (
    <div>
      <h1 className="text-xl font-bold text-slate-100 mb-1">{t.smtpTitle}</h1>
      <p className="text-xs text-slate-500 mb-6">{t.smtpDesc}</p>

      {estado.corrupta && (
        <div role="alert" className="max-w-lg mb-4 text-sm text-red-400 bg-red-900/30 border border-red-800 rounded-lg px-3 py-2">
          {t.smtpCorruptBanner(motivoLegible(estado.motivo))}
        </div>
      )}

      <form onSubmit={guardar} className="max-w-lg space-y-4">
        <div className="grid grid-cols-3 gap-3">
          <div className="col-span-2">
            <label className={LABEL} htmlFor="smtp-host">{t.smtpHost}</label>
            <input id="smtp-host" className={INPUT} value={form.host} onChange={(e) => set('host', e.target.value)} required />
          </div>
          <div>
            <label className={LABEL} htmlFor="smtp-port">{t.smtpPort}</label>
            <input id="smtp-port" type="number" min="1" max="65535" className={INPUT} value={form.port} onChange={(e) => set('port', e.target.value)} required />
          </div>
        </div>
        <div>
          <label className={LABEL} htmlFor="smtp-enc">{t.smtpEncryption}</label>
          <select id="smtp-enc" className={INPUT} value={form.encryption} onChange={(e) => cambiarCifrado(e.target.value)}>
            <option value="tls">{t.smtpEncTls}</option>
            <option value="ssl">{t.smtpEncSsl}</option>
            <option value="none">{t.smtpEncNone}</option>
          </select>
          {form.encryption === 'none' && (
            <p className="mt-1 text-xs text-red-400 bg-red-900/30 border border-red-800 rounded-lg px-3 py-2">{t.smtpEncNoneWarning}</p>
          )}
        </div>
        <div>
          <label className={LABEL} htmlFor="smtp-user">{t.smtpUser}</label>
          <input id="smtp-user" className={INPUT} value={form.user} onChange={(e) => set('user', e.target.value)} required autoComplete="off" />
        </div>
        <div>
          <label className={LABEL} htmlFor="smtp-pass">{t.smtpPassword}</label>
          <PasswordInput id="smtp-pass" className={INPUT} value={form.password} onChange={(e) => set('password', e.target.value)} autoComplete="new-password" />
          {form.password === MASCARA && <p className="mt-1 text-xs text-slate-500">{t.smtpPasswordHint}</p>}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={LABEL} htmlFor="smtp-from-name">{t.smtpFromName}</label>
            <input id="smtp-from-name" className={INPUT} value={form.from_name} onChange={(e) => set('from_name', e.target.value)} required />
          </div>
          <div>
            <label className={LABEL} htmlFor="smtp-from-email">{t.smtpFromEmail}</label>
            <input id="smtp-from-email" type="email" className={INPUT} value={form.from_email} onChange={(e) => set('from_email', e.target.value)} required />
          </div>
        </div>
        <div>
          <label className={LABEL} htmlFor="smtp-test-to-default">{t.smtpTestToDefault}</label>
          <input id="smtp-test-to-default" type="email" className={INPUT} value={form.test_to} onChange={(e) => set('test_to', e.target.value)} autoComplete="off" />
          <p className="mt-1 text-xs text-slate-500">{t.smtpTestToHint}</p>
        </div>

        {resultado && (
          <div role="status" className={`text-sm rounded-lg px-3 py-2 border ${resultado.ok ? 'text-green-400 bg-green-900/30 border-green-800' : 'text-red-400 bg-red-900/30 border-red-800'}`}>
            {resultado.texto}
          </div>
        )}

        <div className="flex flex-wrap gap-2 pt-2">
          <button type="submit" disabled={ocupado !== null} className={`${BOTON} bg-purple-600 hover:bg-purple-700 text-white`}>
            {ocupado === 'guardar' ? t.smtpSaving : t.smtpSave}
          </button>
          <button type="button" onClick={probarConexion} disabled={ocupado !== null} className={`${BOTON} bg-slate-700 hover:bg-slate-600 text-slate-300`}>
            {ocupado === 'conexion' ? t.smtpTesting : t.smtpTestConnection}
          </button>
          <button type="button" onClick={abrirDialogo} disabled={ocupado !== null} className={`${BOTON} bg-slate-700 hover:bg-slate-600 text-slate-300`}>
            {t.smtpSendTest}
          </button>
        </div>
      </form>

      {dialogo.abierto && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={cerrarDialogo}>
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="smtp-test-titulo"
            className="w-full max-w-md bg-slate-900 border border-slate-700 rounded-lg p-5"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => { if (e.key === 'Escape') cerrarDialogo() }}
          >
            <h2 id="smtp-test-titulo" className="text-base font-bold text-slate-100 mb-4">{t.smtpTestModalTitle}</h2>
            <form onSubmit={enviarPrueba} className="space-y-3">
              <div>
                <label className={LABEL} htmlFor="smtp-test-to">{t.smtpTestRecipient}</label>
                <input
                  id="smtp-test-to" type="email" required autoFocus className={INPUT} value={dialogo.to}
                  onChange={(e) => setDialogo((d) => ({ ...d, to: e.target.value, error: null }))}
                />
              </div>
              {dialogo.error && (
                <div role="alert" className="text-sm text-red-400 bg-red-900/30 border border-red-800 rounded-lg px-3 py-2">
                  {dialogo.error}
                </div>
              )}
              <div className="flex justify-end gap-2 pt-2">
                <button type="button" onClick={cerrarDialogo} disabled={ocupado === 'prueba'} className={`${BOTON} bg-slate-700 hover:bg-slate-600 text-slate-300`}>
                  {t.smtpCancel}
                </button>
                <button type="submit" disabled={ocupado === 'prueba' || !dialogo.to.trim()} className={`${BOTON} bg-purple-600 hover:bg-purple-700 text-white`}>
                  {ocupado === 'prueba' ? t.smtpSending : t.smtpTestSendButton}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
