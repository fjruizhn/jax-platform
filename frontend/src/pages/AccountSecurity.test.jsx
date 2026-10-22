import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { MemoryRouter } from 'react-router-dom'
import { useJaxStore } from '../store/useJaxStore'

const api = vi.hoisted(() => ({ status: vi.fn(), stepUp: vi.fn(), stepUpVerify: vi.fn(), enrollmentStart: vi.fn(), reenrollmentStart: vi.fn(), enrollmentConfirm: vi.fn(), regenerateCodes: vi.fn(), disable: vi.fn() }))
vi.mock('../api/virtualOffice', () => ({ mfaApi: api }))
import AccountSecurity from './AccountSecurity'
import { I18nProvider } from '../i18n/index.jsx'

function show() { return render(<I18nProvider><MemoryRouter><AccountSecurity /></MemoryRouter></I18nProvider>) }
beforeEach(() => { vi.clearAllMocks(); api.status.mockResolvedValue({ data: { enabled: true, state: 'ACTIVE' } }); useJaxStore.setState({ token: 'a', user: { user_id: 1, email: 'a@b.c' }, _sessionEpoch: 1, mfaRecoveryCodes: null }) })
describe('Account security MFA', () => {
  it('does not apply a late step-up token after account B replaces account A', async () => {
    let resolve; api.stepUp.mockResolvedValue({ data: { challenge_token: 'c' } }); api.stepUpVerify.mockReturnValue(new Promise((r) => { resolve = r }))
    show(); await screen.findByText('Generar códigos nuevos'); fireEvent.click(screen.getByText('Generar códigos nuevos'))
    fireEvent.change(screen.getByLabelText('Contraseña actual'), { target: { value: 'p' } }); api.regenerateCodes.mockRejectedValue({ response: { status: 403 } }); fireEvent.click(screen.getByText('Confirmar'))
    await screen.findByText('Confirmá con tu autenticador'); fireEvent.change(screen.getByRole('textbox'), { target: { value: '123456' } }); fireEvent.click(screen.getAllByText('Verificar').at(-1))
    useJaxStore.setState({ token: 'b', user: { user_id: 2, email: 'b@b.c' }, _sessionEpoch: 2 }); resolve({ data: { access_token: 'late-a' } })
    await waitFor(() => expect(useJaxStore.getState().token).toBe('b'))
  })
  it('shows post-cut codes without refetching authenticated status and clears them on acknowledgement', async () => {
    useJaxStore.setState({ token: null, user: null, mfaRecoveryCodes: ['a'.repeat(32)], mfaRecoveryCodesOwnerId: 1 })
    show(); expect(screen.getByText('a'.repeat(32))).toBeInTheDocument(); expect(api.status).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText(/Entiendo/)); expect(useJaxStore.getState().mfaRecoveryCodes).toBeNull()
  })
})
