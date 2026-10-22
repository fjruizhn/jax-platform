import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { MemoryRouter } from 'react-router-dom'

const { login, establecerSesion, clearAvisoSesion, verify, recover, recoveryStart, confirm } = vi.hoisted(() => ({
  login: vi.fn(), establecerSesion: vi.fn(), clearAvisoSesion: vi.fn(), verify: vi.fn(), recover: vi.fn(), recoveryStart: vi.fn(), confirm: vi.fn(),
}))
vi.mock('../store/useJaxStore', () => ({ useJaxStore: (pick) => pick({ login, establecerSesion, avisoSesion: null, clearAvisoSesion }) }))
vi.mock('../api/virtualOffice', () => ({ mfaApi: { loginVerify: verify, recover, recoveryEnrollmentStart: recoveryStart, enrollmentConfirm: confirm } }))
vi.mock('../api/client', () => ({ default: { post: vi.fn() } }))
import Login from './Login'
import { I18nProvider } from '../i18n/index.jsx'

function show() { return render(<I18nProvider><MemoryRouter><Login /></MemoryRouter></I18nProvider>) }
function credentials() { fireEvent.change(screen.getByPlaceholderText('nombre@empresa.com'), { target: { value: 'a@b.c' } }); fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'secret' } }); fireEvent.click(screen.getByRole('button', { name: /Entrar/i })) }
beforeEach(() => { vi.clearAllMocks(); login.mockResolvedValue({ mfa_required: true, challenge_token: 'x'.repeat(43), expires_at: 'soon', token_type: 'mfa_challenge' }) })

describe('Login MFA', () => {
  it('keeps a login challenge out of the authenticated store and shows OTP', async () => {
    show(); credentials()
    expect(await screen.findByLabelText('Código de autenticación')).toHaveFocus()
    expect(establecerSesion).not.toHaveBeenCalled()
  })
  it('rejects malformed OTP without consuming the challenge', async () => {
    show(); credentials(); await screen.findByLabelText('Código de autenticación')
    fireEvent.change(screen.getByLabelText('Código de autenticación'), { target: { value: '12x' } }); fireEvent.click(screen.getByRole('button', { name: 'Verificar' }))
    expect(await screen.findByText('Ingresá exactamente seis dígitos.')).toBeInTheDocument(); expect(verify).not.toHaveBeenCalled()
  })
  it('verifies OTP through the shared session helper', async () => {
    verify.mockResolvedValue({ data: { access_token: 'new', account_class: 'INTERNAL' } })
    show(); credentials(); await screen.findByLabelText('Código de autenticación')
    fireEvent.change(screen.getByLabelText('Código de autenticación'), { target: { value: '123456' } }); fireEvent.click(screen.getByRole('button', { name: 'Verificar' }))
    await waitFor(() => expect(establecerSesion).toHaveBeenCalledWith(expect.objectContaining({ access_token: 'new' })))
  })
  it('cancel clears the challenge and returns to password entry', async () => {
    show(); credentials(); await screen.findByLabelText('Código de autenticación'); fireEvent.click(screen.getByText(/Volver al inicio/))
    expect(await screen.findByPlaceholderText('nombre@empresa.com')).toBeInTheDocument()
  })
  it('does not apply a late verification response after cancellation', async () => {
    let resolve
    verify.mockReturnValue(new Promise((r) => { resolve = r }))
    show(); credentials(); await screen.findByLabelText('Código de autenticación')
    fireEvent.change(screen.getByLabelText('Código de autenticación'), { target: { value: '123456' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verificar' }))
    fireEvent.click(screen.getByText(/Volver al inicio/))
    resolve({ data: { access_token: 'late', account_class: 'INTERNAL' } })
    await waitFor(() => expect(screen.getByPlaceholderText('nombre@empresa.com')).toBeInTheDocument())
    expect(establecerSesion).not.toHaveBeenCalled()
  })
  it('recovery chain never creates a session and shows codes only after enrollment confirmation', async () => {
    recover.mockResolvedValue({ data: { recovery_token: 'r'.repeat(43) } }); recoveryStart.mockResolvedValue({ data: { enrollment_token: 'e'.repeat(43), secret: 'SETUP' } }); confirm.mockResolvedValue({ data: { recovery_codes: ['one', 'two'] } })
    show(); credentials(); await screen.findByText('Usar un código de recuperación'); fireEvent.click(screen.getByText('Usar un código de recuperación'))
    fireEvent.change(screen.getByLabelText('Código de recuperación'), { target: { value: 'a'.repeat(32) } }); fireEvent.click(screen.getByRole('button', { name: 'Continuar' }))
    expect(await screen.findByText('SETUP')).toBeInTheDocument(); expect(establecerSesion).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Código de autenticación'), { target: { value: '123456' } }); fireEvent.click(screen.getByRole('button', { name: 'Verificar' }))
    expect(await screen.findByText('one')).toBeInTheDocument(); expect(establecerSesion).not.toHaveBeenCalled()
  })
})
