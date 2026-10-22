import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useI18n } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
import { officeInvitationsApi } from '../api/virtualOffice'
import PasswordInput from '../components/PasswordInput'
import { problemaDePassword } from '../lib/reglasPassword'
import { TAMANO_BOTON_ACCION } from '../tema/botones'
import { codigoDe } from '../api/errores'

const INPUT = 'w-full min-h-11 bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus-visible:ring-2 focus-visible:ring-foco'
const INVITATION_TOKEN_KEY = 'office-invitation-token-v1'

function tokenFromFragment(hash) {
  const value = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash).get('token')
  return typeof value === 'string' && value.length > 0 ? value : null
}

function invitationToken() {
  const fragmentToken = tokenFromFragment(window.location.hash)
  if (fragmentToken) {
    window.sessionStorage.setItem(INVITATION_TOKEN_KEY, fragmentToken)
    return fragmentToken
  }
  return window.sessionStorage.getItem(INVITATION_TOKEN_KEY)
}

export default function OfficeInvitation() {
  const { t } = useI18n()
  const user = useJaxStore((s) => s.user)
  const sessionRestoring = useJaxStore((s) => s.sessionRestoring)
  const sessionEpoch = useJaxStore((s) => s._sessionEpoch)
  const sessionToken = useJaxStore((s) => s.token)
  const navigate = useNavigate()
  const [token] = useState(invitationToken)
  const [newAccount, setNewAccount] = useState(false)
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [passwordError, setPasswordError] = useState(null)
  const requestRef = useRef(null)

  // La URL de correo usa fragmento para que el bearer no llegue al servidor.
  // Tras leerlo lo quitamos además del historial visible y lo mantenemos sólo
  // en memoria hasta el POST de aceptación.
  useEffect(() => {
    if (token && window.location.hash) window.history.replaceState(window.history.state, '', window.location.pathname)
  }, [token])

  // Never let a request initiated as A finish against B. Abort is best effort
  // at the network boundary; the server still treats a lost result as unknown.
  useEffect(() => {
    requestRef.current?.abort()
    requestRef.current = null
    setLoading(false)
    setResult(null)
  }, [sessionEpoch, sessionToken, user?.user_id])

  const useExisting = Boolean(user && sessionToken)
  const canSubmit = !sessionRestoring && token && (useExisting || (newAccount && password.length > 0 && password === confirm))

  async function submit(event) {
    event.preventDefault()
    if (!canSubmit || loading) return
    const passwordProblem = !useExisting ? problemaDePassword(password) : null
    if (passwordProblem) {
      setPasswordError(passwordProblem)
      return
    }
    if (!useExisting && password !== confirm) return
    const snapshot = { epoch: sessionEpoch, token: sessionToken, userId: user?.user_id ?? null }
    const sameSession = () => {
      const current = useJaxStore.getState()
      return current._sessionEpoch === snapshot.epoch && current.token === snapshot.token && (current.user?.user_id ?? null) === snapshot.userId
    }
    const controller = new AbortController()
    requestRef.current = controller
    setLoading(true)
    setResult(null)
    setPasswordError(null)
    try {
      const payload = useExisting ? { token } : { token, password }
      await officeInvitationsApi.accept(payload, { accessToken: useExisting ? snapshot.token : null, signal: controller.signal })
      if (!sameSession() || requestRef.current !== controller) return
      setPassword('')
      setConfirm('')
      window.sessionStorage.removeItem(INVITATION_TOKEN_KEY)
      setResult('accepted')
    } catch (err) {
      if (!sameSession() || requestRef.current !== controller) return
      if (err?.response?.status === 409 && codigoDe(err) === 'office_invitation_existing_session_required') setResult('login')
      else if (!err?.response || err?.response?.status >= 500) setResult('ambiguous')
      else setResult('error')
    } finally {
      if (requestRef.current === controller) {
        requestRef.current = null
        if (sameSession()) setLoading(false)
      }
    }
  }

  function continueWithLogin(recovery) {
    navigate('/login', { state: { officeInvitationFlow: recovery ? 'recovery' : 'accept' } })
  }

  return (
    <main data-foco-inicial tabIndex={-1} className="min-h-dvh bg-fondo p-4 text-texto sm:p-6">
      <section className="mx-auto max-w-md rounded-xl border border-borde bg-superficie p-5 sm:p-6">
        <h1 className="text-xl font-bold">{t.officeInvitation.title}</h1>
        {!token && <p role="alert" className="mt-3 text-sm text-peligro">{t.officeInvitation.invalidLink}</p>}
        {token && sessionRestoring && <p role="status" className="mt-3 text-sm text-texto-suave">{t.officeInvitation.checkingSession}</p>}
        {token && !sessionRestoring && <form onSubmit={submit} className="mt-4 space-y-4">
          {useExisting ? <p className="text-sm text-texto-suave">{t.officeInvitation.signedInAs(user.email)}</p> : <>
            {!newAccount ? <>
              <p className="text-sm text-texto-suave">{t.officeInvitation.newAccountIntro}</p>
              <button type="button" onClick={() => setNewAccount(true)} className={`${TAMANO_BOTON_ACCION} rounded bg-accion px-4 text-sobre-color`}>{t.officeInvitation.createAccount}</button>
            </> : <>
              <p className="text-sm text-texto-suave">{t.officeInvitation.newPasswordIntro}</p>
              <label className="block text-sm text-texto-suave" htmlFor="office-invitation-password">{t.officeInvitation.newPassword}
                <PasswordInput id="office-invitation-password" value={password} onChange={(event) => { setPassword(event.target.value); setPasswordError(null) }} autoComplete="new-password" aria-invalid={Boolean(passwordError)} aria-describedby={passwordError ? 'office-invitation-password-error' : undefined} className={`${INPUT} mt-1`} required />
              </label>
              <label className="block text-sm text-texto-suave" htmlFor="office-invitation-password-confirm">{t.officeInvitation.confirmPassword}
                <PasswordInput id="office-invitation-password-confirm" value={confirm} onChange={(event) => setConfirm(event.target.value)} autoComplete="new-password" className={`${INPUT} mt-1`} required />
              </label>
              {passwordError && <p id="office-invitation-password-error" role="alert" className="text-sm text-peligro">{passwordError === 'corta' ? t.resetPasswordShort : t.resetPasswordLong}</p>}
              {password && confirm && password !== confirm && <p role="alert" className="text-sm text-peligro">{t.officeInvitation.passwordMismatch}</p>}
            </>}
          </>}
          {result === 'accepted' && <p role="status" className="text-sm text-exito">{t.officeInvitation.accepted}</p>}
          {result === 'error' && <p role="alert" className="text-sm text-peligro">{t.officeInvitation.error}</p>}
          {result === 'login' && <div className="space-y-3"><p role="status" className="text-sm text-texto-suave">{t.officeInvitation.loginRequired}</p><button type="button" onClick={() => continueWithLogin(false)} className={`${TAMANO_BOTON_ACCION} w-full rounded bg-accion text-sobre-color`}>{t.officeInvitation.login}</button></div>}
          {result === 'ambiguous' && <div className="space-y-3"><p role="status" className="text-sm text-texto-suave">{t.officeInvitation.ambiguous}</p><button type="button" onClick={() => continueWithLogin(true)} className={`${TAMANO_BOTON_ACCION} w-full rounded bg-accion text-sobre-color`}>{t.officeInvitation.login}</button></div>}
          {!useExisting && !newAccount && !result && <button type="button" onClick={() => continueWithLogin(false)} className={`${TAMANO_BOTON_ACCION} w-full rounded border border-borde text-texto hover:bg-hundido`}>{t.officeInvitation.login}</button>}
          {!['login', 'ambiguous'].includes(result) && (useExisting || newAccount) && <button type="submit" disabled={!canSubmit || loading} className={`${TAMANO_BOTON_ACCION} w-full rounded bg-accion text-sobre-color disabled:opacity-60`}>{loading ? t.officeInvitation.accepting : t.officeInvitation.accept}</button>}
        </form>}
      </section>
    </main>
  )
}
