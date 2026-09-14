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
const VACIO = { host: '', port: 587, encryption: 'tls', user: '', password: '', from_name: '', from_email: '' }
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
    })
    setEstado({ corrupta: data.corrupta, motivo: data.motivo })
  }

  useEffect(() => {
    cargar().catch((err) => setResultado({ ok: false, texto: mensaje(err) }))
  }, [])

  function set(campo, valor) {
    setForm((f) => ({ ...f, [campo]: valor }))
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

  function enviarPrueba() {
    accion('prueba', () => api.post('/admin/smtp/test'), (data) => t.smtpTestSent(data?.to))
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
          <select id="smtp-enc" className={INPUT} value={form.encryption} onChange={(e) => set('encryption', e.target.value)}>
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
          <button type="button" onClick={enviarPrueba} disabled={ocupado !== null} className={`${BOTON} bg-slate-700 hover:bg-slate-600 text-slate-300`}>
            {ocupado === 'prueba' ? t.smtpSending : t.smtpSendTest}
          </button>
        </div>
      </form>
    </div>
  )
}
