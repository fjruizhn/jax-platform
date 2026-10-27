import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { MemoryRouter, useLocation } from 'react-router-dom'

const { login, establecerSesion, clearAvisoSesion, verify, recover, recoveryEnrollmentStart, confirm } = vi.hoisted(() => ({
  login: vi.fn(), establecerSesion: vi.fn(), clearAvisoSesion: vi.fn(), verify: vi.fn(), recover: vi.fn(), recoveryEnrollmentStart: vi.fn(), confirm: vi.fn(),
}))
vi.mock('../store/useJaxStore', () => ({ useJaxStore: (pick) => pick({ login, establecerSesion, avisoSesion: null, clearAvisoSesion }) }))
vi.mock('../api/virtualOffice', () => ({ mfaApi: { loginVerify: verify, recover, recoveryEnrollmentStart, enrollmentConfirm: confirm } }))
vi.mock('../api/client', () => ({ default: { post: vi.fn() } }))
import Login from './Login'
import { I18nProvider } from '../i18n/index.jsx'

function Location() { return <output data-testid="location">{useLocation().pathname}</output> }
function show(entry = '/login') { return render(<I18nProvider><MemoryRouter initialEntries={[entry]}><Login /><Location /></MemoryRouter></I18nProvider>) }
function credentials() { fireEvent.change(screen.getByPlaceholderText('nombre@empresa.com'), { target: { value: 'a@b.c' } }); fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'secret' } }); fireEvent.click(screen.getByRole('button', { name: /Entrar/i })) }
beforeEach(() => { vi.clearAllMocks(); login.mockResolvedValue({ mfa_required: true, challenge_token: 'x'.repeat(43), expires_at: 'soon', token_type: 'mfa_challenge' }) })

describe('Login MFA', () => {
  it('shows a scannable QR during recovery enrollment and clears it on cancellation', async () => {
    recover.mockResolvedValue({ data: { recovery_token: 'r'.repeat(43), expires_at: 'soon' } })
    recoveryEnrollmentStart.mockResolvedValue({ data: { enrollment_token: 'e'.repeat(43), secret: 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP' } })
    show(); credentials()
    await screen.findByText('Usar un código de recuperación')
    fireEvent.click(screen.getByText('Usar un código de recuperación'))
    fireEvent.change(screen.getByLabelText('Código de recuperación'), { target: { value: 'a'.repeat(32) } })
    fireEvent.click(screen.getByRole('button', { name: 'Continuar' }))
    expect(await screen.findByRole('img', { name: 'Escaneá este código QR con tu aplicación autenticadora.' })).toBeInTheDocument()
    fireEvent.click(screen.getByText(/Volver al inicio/))
    expect(screen.queryByRole('img', { name: 'Escaneá este código QR con tu aplicación autenticadora.' })).not.toBeInTheDocument()
  })
  it('keeps a login challenge out of the authenticated store and shows OTP', async () => {
    show(); credentials()
    expect(await screen.findByLabelText('Código de autenticación')).toBeInTheDocument()
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
  it('returns to Virtual Office after MFA when that was the protected destination', async () => {
    verify.mockResolvedValue({ data: { access_token: 'new', account_class: 'OFFICE_INTERNAL' } })
    show({ pathname: '/login', state: { from: { pathname: '/virtual-office' } } }); credentials(); await screen.findByLabelText('Código de autenticación')
    fireEvent.change(screen.getByLabelText('Código de autenticación'), { target: { value: '123456' } }); fireEvent.click(screen.getByRole('button', { name: 'Verificar' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/virtual-office'))
  })
  it('returns an invitation sign-in to the protected acceptance route after MFA', async () => {
    verify.mockResolvedValue({ data: { access_token: 'new', account_class: 'OFFICE_INTERNAL' } })
    show({ pathname: '/login', state: { officeInvitationFlow: 'accept' } }); credentials(); await screen.findByLabelText('Código de autenticación')
    fireEvent.change(screen.getByLabelText('Código de autenticación'), { target: { value: '123456' } }); fireEvent.click(screen.getByRole('button', { name: 'Verificar' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/office/invitation'))
  })
  it('takes an ambiguous invitation recovery to Virtual Office after MFA', async () => {
    verify.mockResolvedValue({ data: { access_token: 'new', account_class: 'OFFICE_INTERNAL' } })
    show({ pathname: '/login', state: { officeInvitationFlow: 'recovery' } }); credentials(); await screen.findByLabelText('Código de autenticación')
    fireEvent.change(screen.getByLabelText('Código de autenticación'), { target: { value: '123456' } }); fireEvent.click(screen.getByRole('button', { name: 'Verificar' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/virtual-office'))
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
  it('uses the two one-shot recovery capabilities in order and never creates a session', async () => {
    recover.mockResolvedValue({ data: { recovery_token: 'r'.repeat(43), expires_at: 'soon' } }); recoveryEnrollmentStart.mockResolvedValue({ data: { enrollment_token: 'e'.repeat(43), secret: 'SETUP', expires_at: 'soon' } }); confirm.mockResolvedValue({ data: { recovery_codes: ['one', 'two'] } })
    show(); credentials(); await screen.findByText('Usar un código de recuperación'); fireEvent.click(screen.getByText('Usar un código de recuperación'))
    fireEvent.change(screen.getByLabelText('Código de recuperación'), { target: { value: 'a'.repeat(32) } }); fireEvent.click(screen.getByRole('button', { name: 'Continuar' }))
    expect(await screen.findByText('SETUP')).toBeInTheDocument(); expect(recover).toHaveBeenCalledWith('a'.repeat(32)); expect(recoveryEnrollmentStart).toHaveBeenCalledWith('r'.repeat(43)); expect(recover.mock.invocationCallOrder[0]).toBeLessThan(recoveryEnrollmentStart.mock.invocationCallOrder[0]); expect(establecerSesion).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Código de autenticación'), { target: { value: '123456' } }); fireEvent.click(screen.getByRole('button', { name: 'Verificar' }))
    expect(await screen.findByText('one')).toBeInTheDocument(); expect(establecerSesion).not.toHaveBeenCalled()
  })
  it('clears the code and does not retry after a dropped recovery response', async () => {
    recover.mockRejectedValue(new Error('dropped'))
    show(); credentials(); await screen.findByText('Usar un código de recuperación'); fireEvent.click(screen.getByText('Usar un código de recuperación'))
    fireEvent.change(screen.getByLabelText('Código de recuperación'), { target: { value: 'a'.repeat(32) } }); fireEvent.click(screen.getByRole('button', { name: 'Continuar' }))
    await screen.findByText('No pudimos confirmar la recuperación. Usá otro código de recuperación sin usar o contactá a soporte.')
    expect(screen.getByLabelText('Código de recuperación')).toHaveValue(''); expect(recover).toHaveBeenCalledTimes(1); expect(recoveryEnrollmentStart).not.toHaveBeenCalled(); expect(screen.queryByLabelText('Código de autenticación')).not.toBeInTheDocument()
  })
  it('clears both capabilities and does not retry after a dropped enrollment-start response', async () => {
    recover.mockResolvedValue({ data: { recovery_token: 'r'.repeat(43), expires_at: 'soon' } }); recoveryEnrollmentStart.mockRejectedValue(new Error('dropped'))
    show(); credentials(); await screen.findByText('Usar un código de recuperación'); fireEvent.click(screen.getByText('Usar un código de recuperación'))
    fireEvent.change(screen.getByLabelText('Código de recuperación'), { target: { value: 'a'.repeat(32) } }); fireEvent.click(screen.getByRole('button', { name: 'Continuar' }))
    await screen.findByText('No pudimos confirmar la recuperación. Usá otro código de recuperación sin usar o contactá a soporte.')
    expect(screen.getByLabelText('Código de recuperación')).toHaveValue(''); expect(recover).toHaveBeenCalledTimes(1); expect(recoveryEnrollmentStart).toHaveBeenCalledTimes(1); expect(screen.queryByLabelText('Código de autenticación')).not.toBeInTheDocument()
  })
  it.each([
    [{ response: { status: 401, data: { detail: 'recovery_invalid' } } }, 'Ingresá un código de recuperación válido.'],
    [{ response: { status: 503, data: { detail: 'office_mfa_unavailable' } } }, 'La verificación en dos pasos no está disponible ahora.'],
    [{ response: { status: 410, data: { detail: 'recovery_expired' } } }, 'Este código de recuperación ya no puede usarse. Contactá a soporte para recuperar el acceso.'],
  ])('renders definite recovery outcome', async (failure, message) => {
    recover.mockRejectedValue(failure)
    show(); credentials(); await screen.findByText('Usar un código de recuperación'); fireEvent.click(screen.getByText('Usar un código de recuperación'))
    fireEvent.change(screen.getByLabelText('Código de recuperación'), { target: { value: 'a'.repeat(32) } }); fireEvent.click(screen.getByRole('button', { name: 'Continuar' }))
    expect(await screen.findByText(message)).toBeInTheDocument()
  })
  it('treats a replayed recovery capability as terminal and never shows confirmation', async () => {
    recover.mockResolvedValue({ data: { recovery_token: 'r'.repeat(43), expires_at: 'soon' } }); recoveryEnrollmentStart.mockRejectedValue({ response: { status: 401, data: { detail: 'challenge_consumed' } } })
    show(); credentials(); await screen.findByText('Usar un código de recuperación'); fireEvent.click(screen.getByText('Usar un código de recuperación'))
    fireEvent.change(screen.getByLabelText('Código de recuperación'), { target: { value: 'a'.repeat(32) } }); fireEvent.click(screen.getByRole('button', { name: 'Continuar' }))
    expect(await screen.findByText('Este código de recuperación ya no puede usarse. Contactá a soporte para recuperar el acceso.')).toBeInTheDocument(); expect(screen.queryByLabelText('Código de autenticación')).not.toBeInTheDocument()
  })
  it('ignores a late recovery response after cancellation', async () => {
    let resolve
    recover.mockReturnValue(new Promise((r) => { resolve = r }))
    show(); credentials(); await screen.findByText('Usar un código de recuperación'); fireEvent.click(screen.getByText('Usar un código de recuperación'))
    fireEvent.change(screen.getByLabelText('Código de recuperación'), { target: { value: 'a'.repeat(32) } }); fireEvent.click(screen.getByRole('button', { name: 'Continuar' }))
    fireEvent.click(screen.getByText(/Volver al inicio/)); resolve({ data: { recovery_token: 'r'.repeat(43) } })
    expect(await screen.findByPlaceholderText('nombre@empresa.com')).toBeInTheDocument(); expect(recoveryEnrollmentStart).not.toHaveBeenCalled(); expect(screen.queryByText('SETUP')).not.toBeInTheDocument()
  })
  it('ignores a late enrollment-start response after cancellation', async () => {
    let resolve
    recover.mockResolvedValue({ data: { recovery_token: 'r'.repeat(43), expires_at: 'soon' } }); recoveryEnrollmentStart.mockReturnValue(new Promise((r) => { resolve = r }))
    show(); credentials(); await screen.findByText('Usar un código de recuperación'); fireEvent.click(screen.getByText('Usar un código de recuperación'))
    fireEvent.change(screen.getByLabelText('Código de recuperación'), { target: { value: 'a'.repeat(32) } }); fireEvent.click(screen.getByRole('button', { name: 'Continuar' }))
    await waitFor(() => expect(recoveryEnrollmentStart).toHaveBeenCalledTimes(1)); fireEvent.click(screen.getByText(/Volver al inicio/)); resolve({ data: { enrollment_token: 'e'.repeat(43), secret: 'SETUP' } })
    expect(await screen.findByPlaceholderText('nombre@empresa.com')).toBeInTheDocument(); expect(screen.queryByText('SETUP')).not.toBeInTheDocument()
  })
  it('clears an expired enrollment capability and returns to login', async () => {
    recover.mockResolvedValue({ data: { recovery_token: 'r'.repeat(43) } }); recoveryEnrollmentStart.mockResolvedValue({ data: { enrollment_token: 'e'.repeat(43), secret: 'SETUP' } }); confirm.mockRejectedValue({ response: { status: 401, data: { detail: 'enrollment_expired' } } })
    show(); credentials(); await screen.findByText('Usar un código de recuperación'); fireEvent.click(screen.getByText('Usar un código de recuperación'))
    fireEvent.change(screen.getByLabelText('Código de recuperación'), { target: { value: 'a'.repeat(32) } }); fireEvent.click(screen.getByRole('button', { name: 'Continuar' }))
    await screen.findByText('SETUP'); fireEvent.change(screen.getByLabelText('Código de autenticación'), { target: { value: '123456' } }); fireEvent.click(screen.getByRole('button', { name: 'Verificar' }))
    expect(await screen.findByPlaceholderText('nombre@empresa.com')).toBeInTheDocument()
  })
  it('locks an ambiguous enrollment confirmation rather than retrying it', async () => {
    recover.mockResolvedValue({ data: { recovery_token: 'r'.repeat(43) } }); recoveryEnrollmentStart.mockResolvedValue({ data: { enrollment_token: 'e'.repeat(43), secret: 'SETUP' } }); confirm.mockRejectedValue(new Error('network lost'))
    show(); credentials(); await screen.findByText('Usar un código de recuperación'); fireEvent.click(screen.getByText('Usar un código de recuperación'))
    fireEvent.change(screen.getByLabelText('Código de recuperación'), { target: { value: 'a'.repeat(32) } }); fireEvent.click(screen.getByRole('button', { name: 'Continuar' }))
    await screen.findByText('SETUP'); fireEvent.change(screen.getByLabelText('Código de autenticación'), { target: { value: '123456' } }); fireEvent.click(screen.getByRole('button', { name: 'Verificar' }))
    await screen.findByText('No se pudo confirmar el resultado. Revisá el estado antes de volver a intentarlo.')
    expect(screen.getByRole('button', { name: 'Verificar' })).toBeDisabled()
  })
})
