import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useJaxStore } from '../store/useJaxStore'
import { useI18n } from '../i18n/index.jsx'
import { useNombreDelSistema } from '../store/useApariencia'
import { mfaApi } from '../api/virtualOffice'
import api from '../api/client'
import { codigoDe } from '../api/errores'
import PasswordInput from '../components/PasswordInput'
import AlertaError from '../components/AlertaError'
import HalEye from '../components/HalEye/HalEye'
import { TAMANO_BOTON_ACCION, TAMANO_MINIMO_TOQUE } from '../tema/botones'

const INPUT = 'w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus:border-foco'
const LABEL = 'block text-xs text-texto-suave mb-1 font-semibold uppercase tracking-wider'
const otpValido = (value) => /^[0-9]{6}$/.test(value)
const restringida = (data) => ['OFFICE_INTERNAL', 'EXTERNAL_CLIENT'].includes(data?.account_class)
const errorMfa = (err, t) => {
  if (err?.response?.status === 503 || codigoDe(err) === 'office_mfa_unavailable') return t.mfa.unavailable
  if (['challenge_expired', 'challenge_consumed', 'capability_expired', 'capability_consumed'].includes(codigoDe(err))) return t.mfa.expired
  return t.mfa.failed
}
const desafioTerminal = (err) => ['challenge_invalid', 'challenge_unavailable', 'challenge_expired', 'challenge_exhausted', 'challenge_version', 'mfa_subject_invalid'].includes(codigoDe(err))
const enrollmentTerminal = (err) => ['enrollment_invalid', 'enrollment_expired', 'enrollment_version', 'challenge_exhausted', 'challenge_unavailable', 'challenge_version'].includes(codigoDe(err))

export default function Login() {
  const login = useJaxStore((s) => s.login)
  const establecerSesion = useJaxStore((s) => s.establecerSesion)
  const avisoSesion = useJaxStore((s) => s.avisoSesion)
  const clearAvisoSesion = useJaxStore((s) => s.clearAvisoSesion)
  const sessionEpoch = useJaxStore((s) => s._sessionEpoch)
  const currentEpoch = () => useJaxStore.getState?.()._sessionEpoch ?? sessionEpoch
  const sesionPropia = (data) => {
    const state = useJaxStore.getState?.()
    return !!state && state.token === data?.access_token && state.user?.user_id === data?.user_id
  }
  const { lang, setLang, t } = useI18n()
  const nombre = useNombreDelSistema(t)
  const navigate = useNavigate()
  const location = useLocation()
  const [view, setView] = useState('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [otp, setOtp] = useState('')
  const [recoveryCode, setRecoveryCode] = useState('')
  const [recoveryTerminal, setRecoveryTerminal] = useState(false)
  const [challenge, setChallenge] = useState(null)
  const [enrollment, setEnrollment] = useState(null)
  const [enrollmentUnknown, setEnrollmentUnknown] = useState(false)
  const [codes, setCodes] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [forgot, setForgot] = useState(false)
  const [forgotEmail, setForgotEmail] = useState('')
  const [forgotError, setForgotError] = useState('')
  const [forgotSent, setForgotSent] = useState(false)
  const otpRef = useRef(null)
  const generation = useRef(0)
  useEffect(() => () => { generation.current += 1; setPassword(''); setOtp(''); setRecoveryCode(''); setChallenge(null); setEnrollment(null); setCodes(null) }, [])
  useEffect(() => { if (view === 'challenge' || view === 'setup') otpRef.current?.focus() }, [view])
  const clearTransient = () => { setChallenge(null); setEnrollment(null); setEnrollmentUnknown(false); setOtp(''); setRecoveryCode(''); setRecoveryTerminal(false) }
  // Local cancellation only prevents this browser from applying a late response;
  // one-shot server operations are never assumed to have rolled back.
  const cancel = () => { generation.current += 1; clearTransient(); setError(''); setView('login') }
  const requestCancel = cancel
  const destination = (data) => {
    const invitationFlow = location.state?.officeInvitationFlow
    if (invitationFlow === 'recovery') return navigate('/virtual-office')
    if (invitationFlow === 'accept') return navigate('/office/invitation')
    return navigate(location.state?.from?.pathname === '/virtual-office' ? '/virtual-office' : (restringida(data) ? '/account/security' : '/'))
  }
  async function submitLogin(e) {
    e.preventDefault(); setError(''); clearAvisoSesion(); setLoading(true); const request = ++generation.current; const epoch = sessionEpoch
    try {
      const data = await login(email, password)
      if (request !== generation.current || (epoch !== currentEpoch() && !sesionPropia(data))) return
      setPassword('')
      if (data?.mfa_required) { setChallenge(data); setView('challenge') } else destination(data)
    } catch (err) {
      if (request !== generation.current || epoch !== currentEpoch()) return
      const status = err.response?.status
      if (status === 423) { const s = err.response?.data?.detail?.retry_after_seconds; setError(Number.isFinite(s) ? t.accountLockedMinutes(Math.ceil(s / 60)) : t.accountLocked) }
      else if (status === 429) { const s = parseInt(err.response?.headers?.['retry-after'], 10); setError(Number.isFinite(s) ? t.tooManyAttemptsSeconds(s) : t.tooManyAttempts) }
      else if (codigoDe(err) === 'ajuste_ilegible') setError(t.ajuste_ilegible)
      else if (status >= 500) setError(t.error_del_servidor)
      else setError(t.loginError)
    } finally { if (request === generation.current) setLoading(false) }
  }
  async function verifyLogin(e) {
    e.preventDefault(); setError(''); const request = ++generation.current; const epoch = sessionEpoch
    if (!otpValido(otp)) return setError(t.mfa.invalidOtp)
    setLoading(true)
    try { const { data } = await mfaApi.loginVerify(challenge.challenge_token, otp); if (request !== generation.current || epoch !== currentEpoch()) return; establecerSesion(data); clearTransient(); destination(data) }
    catch (err) { if (request === generation.current && epoch === currentEpoch()) { if (desafioTerminal(err)) { clearTransient(); setView('login') }; setError(errorMfa(err, t)) } } finally { if (request === generation.current && epoch === currentEpoch()) setLoading(false) }
  }
  async function recover(e) {
    e.preventDefault(); setError(''); const request = ++generation.current; const epoch = sessionEpoch
    if (!/^[A-Za-z0-9_-]{32}$/.test(recoveryCode)) return setError(t.mfa.invalidRecovery)
    let recoveryAccepted = false
    setLoading(true)
    try {
      const recovered = await mfaApi.recover(recoveryCode)
      if (request !== generation.current || epoch !== currentEpoch()) return
      recoveryAccepted = true
      // Both capabilities are one-shot.  The recovery bearer never reaches the
      // UI state: it is used immediately and is discarded on every outcome.
      setRecoveryCode('')
      const start = await mfaApi.recoveryEnrollmentStart(recovered.data.recovery_token)
      if (request !== generation.current || epoch !== currentEpoch()) return
      setEnrollmentUnknown(false); setEnrollment(start.data); setView('setup')
    } catch (err) {
      if (request === generation.current && epoch === currentEpoch()) {
        if (err?.response?.status === 401 && !recoveryAccepted) setError(t.mfa.invalidRecovery)
        else if (err?.response?.status === 401) {
          clearTransient()
          setError(t.mfa.recoveryAssistance)
        }
        else if (err?.response?.status === 410) {
          clearTransient()
          setRecoveryTerminal(true)
          setError(t.mfa.recoveryAssistance)
        } else if (err?.response?.status === 503 && codigoDe(err) === 'office_mfa_unavailable') {
          clearTransient()
          setError(errorMfa(err, t))
        } else {
          clearTransient()
          setError(t.mfa.recoveryIndeterminate)
        }
      }
    } finally { if (request === generation.current && epoch === currentEpoch()) setLoading(false) }
  }
  async function confirmEnrollment(e) {
    e.preventDefault(); setError(''); const request = ++generation.current; const epoch = sessionEpoch
    if (!otpValido(otp)) return setError(t.mfa.invalidOtp)
    setLoading(true)
    try { const { data } = await mfaApi.enrollmentConfirm(enrollment.enrollment_token, otp); if (request !== generation.current || epoch !== currentEpoch()) return; setEnrollment(null); setEnrollmentUnknown(false); setOtp(''); setCodes(data.recovery_codes); setView('codes') }
    catch (err) { if (request === generation.current && epoch === currentEpoch()) { if (enrollmentTerminal(err)) { setEnrollment(null); setOtp(''); setView('login') } else if (!err?.response || err.response.status >= 500) { setEnrollmentUnknown(true); setError(t.mfa.actionUncertain); return } setError(errorMfa(err, t)) } } finally { if (request === generation.current && epoch === currentEpoch()) setLoading(false) }
  }
  async function forgotPassword(e) {
    e.preventDefault(); setForgotError(''); setLoading(true)
    try { await api.post('/auth/forgot-password', { email: forgotEmail }); setForgotSent(true) }
    catch (err) { if (err?.response?.status === 429) { const s = parseInt(err.response.headers?.['retry-after'], 10); setForgotError(Number.isFinite(s) ? t.tooManyAttemptsSeconds(s) : t.tooManyAttempts) } else setForgotSent(true) }
    finally { setLoading(false) }
  }
  const language = <div className="flex justify-end gap-1 mb-4">{['es', 'en'].map((l) => <button key={l} type="button" onClick={() => setLang(l)} aria-label={l} className={`${TAMANO_MINIMO_TOQUE} px-1.5 py-0.5 rounded text-xs font-bold uppercase transition-colors ${lang === l ? 'bg-accion text-sobre-color' : 'text-texto-tenue hover:text-texto'}`}>{l}</button>)}</div>
  const failure = error && <AlertaError className="text-sm bg-peligro-fondo border border-peligro-borde rounded-lg px-3 py-2">{error}</AlertaError>
  const back = <button type="button" onClick={requestCancel} className="w-full text-xs min-h-6 text-texto-tenue hover:text-texto transition-colors">← {t.mfa.back}</button>
  const otpField = (id) => <div><label htmlFor={id} className={LABEL}>{t.mfa.otpLabel}</label><input ref={otpRef} id={id} value={otp} onChange={(e) => setOtp(e.target.value.replace(/[^0-9]/g, '').slice(0, 6))} inputMode="numeric" autoComplete="one-time-code" className={INPUT} required /></div>
  return <div className="min-h-dvh bg-fondo flex items-center justify-center p-4"><div className="w-full max-w-md">{language}
    {view === 'login' && (forgot ? <section className="bg-superficie rounded-xl p-6 border border-borde space-y-4"><h1 className="text-xl font-bold text-texto">{t.forgotPasswordTitle}</h1><p className="text-sm text-texto-tenue">{t.forgotPasswordDesc}</p>{forgotSent ? <p className="text-sm text-exito">{t.forgotPasswordSent}</p> : <form onSubmit={forgotPassword} className="space-y-4"><label htmlFor="forgot-email" className={LABEL}>{t.emailLabel}</label><input id="forgot-email" type="email" value={forgotEmail} onChange={(e) => setForgotEmail(e.target.value)} className={INPUT} required autoFocus />{forgotError && <AlertaError>{forgotError}</AlertaError>}<button disabled={loading} className={`${TAMANO_BOTON_ACCION} w-full bg-accion text-sobre-color rounded`}>{t.forgotPasswordSend}</button></form>}<button type="button" onClick={() => setForgot(false)} className="w-full text-xs min-h-6 text-texto-tenue">← {t.backToLogin}</button></section> : <><div className="flex justify-center mb-8"><HalEye size={150} reposo /></div><h1 className="text-center text-2xl font-bold text-texto mb-1">{nombre}</h1><p className="text-center text-xs text-texto-tenue mb-8">{t.loginTagline}</p>{avisoSesion && <AlertaError className="text-sm bg-peligro-fondo border border-peligro-borde rounded-lg px-3 py-2 mb-4">{t[avisoSesion] ?? t.loginError}</AlertaError>}<form onSubmit={submitLogin} className="bg-superficie rounded-xl p-6 border border-borde space-y-4"><div><label htmlFor="login-email" className={LABEL}>{t.emailLabel}</label><input id="login-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} className={INPUT} placeholder={t.emailPlaceholder} required autoFocus /></div><div><label htmlFor="login-password" className={LABEL}>{t.passwordLabel}</label><PasswordInput id="login-password" value={password} onChange={(e) => setPassword(e.target.value)} className={INPUT} placeholder="••••••••" autoComplete="current-password" required /></div>{failure}<button disabled={loading} className="w-full py-2.5 rounded-lg bg-accion hover:bg-accion-hover disabled:opacity-50 text-sobre-color font-semibold transition-colors">{loading ? t.loggingIn : t.loginButton(nombre)}</button><button type="button" onClick={() => setForgot(true)} className="w-full text-xs min-h-6 text-texto-tenue">{t.forgotPassword}</button></form></>)}
    {view === 'challenge' && <section className="bg-superficie rounded-xl p-6 border border-borde space-y-4"><h1 className="text-xl font-bold text-texto">{t.mfa.title}</h1><p className="text-sm text-texto-tenue">{t.mfa.challengeIntro}</p><form onSubmit={verifyLogin} className="space-y-4">{otpField('mfa-otp')}{failure}<button disabled={loading} className="w-full py-2.5 rounded-lg bg-accion text-sobre-color font-semibold disabled:opacity-50">{loading ? t.mfa.verifying : t.mfa.verify}</button></form><button type="button" onClick={() => { generation.current += 1; setOtp(''); setError(''); setView('recovery') }} className="w-full min-h-6 text-xs text-info hover:text-info">{t.mfa.recoveryLink}</button>{back}</section>}
    {view === 'recovery' && <section className="bg-superficie rounded-xl p-6 border border-borde space-y-4"><h1 className="text-xl font-bold text-texto">{t.mfa.recoveryTitle}</h1><p className="text-sm text-texto-tenue">{t.mfa.recoveryIntro}</p><form onSubmit={recover} className="space-y-4"><div><label htmlFor="recovery-code" className={LABEL}>{t.mfa.recoveryLabel}</label><input id="recovery-code" value={recoveryCode} onChange={(e) => { setError(''); setRecoveryCode(e.target.value) }} autoComplete="one-time-code" className={INPUT} required disabled={recoveryTerminal} /></div>{failure}<button disabled={loading || recoveryTerminal} className="w-full py-2.5 rounded-lg bg-accion text-sobre-color font-semibold disabled:opacity-50">{t.mfa.continue}</button></form>{back}</section>}
    {view === 'setup' && <section className="bg-superficie rounded-xl p-6 border border-borde space-y-4"><h1 className="text-xl font-bold text-texto">{t.mfa.setupTitle}</h1><p className="text-sm text-texto-tenue">{t.mfa.setupIntro}</p><div><p className={LABEL}>{t.mfa.secretLabel}</p><code className="block break-all bg-hundido border border-borde rounded-lg p-3 text-sm text-texto">{enrollment?.secret}</code></div><form onSubmit={confirmEnrollment} className="space-y-4">{otpField('setup-otp')}{failure}<button disabled={loading || enrollmentUnknown} className="w-full py-2.5 rounded-lg bg-accion text-sobre-color font-semibold disabled:opacity-50">{t.mfa.verify}</button></form>{back}</section>}
    {view === 'codes' && <section className="bg-superficie rounded-xl p-6 border border-borde space-y-4"><h1 className="text-xl font-bold text-texto">{t.mfa.codesTitle}</h1><p className="text-sm text-texto-tenue">{t.mfa.codesIntro}</p><ul className="grid grid-cols-1 sm:grid-cols-2 gap-2" aria-label={t.mfa.codesTitle}>{codes?.map((code) => <li key={code} className="break-all bg-hundido border border-borde rounded p-2 font-mono text-sm text-texto">{code}</li>)}</ul><button type="button" onClick={() => { setCodes(null); cancel() }} className="w-full py-2.5 rounded-lg bg-accion text-sobre-color font-semibold">{t.mfa.acknowledge}</button></section>}
  </div></div>
}
