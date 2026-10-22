import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { I18nProvider } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'

const invitations = vi.hoisted(() => ({ accept: vi.fn() }))
vi.mock('../api/virtualOffice', () => ({ officeInvitationsApi: invitations }))

import OfficeInvitation from './OfficeInvitation'

function Location() { return <div data-testid="location">{useLocation().pathname}</div> }
function show(hash = '#token=opaque-bearer') {
  window.history.replaceState({}, '', `/office/invitation${hash}`)
  return render(<I18nProvider><MemoryRouter><OfficeInvitation /><Location /></MemoryRouter></I18nProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  window.sessionStorage.clear()
  useJaxStore.setState({ token: null, user: null, sessionRestoring: false, _sessionEpoch: 1 })
})

describe('Office invitation acceptance', () => {
  it('reads the bearer from the fragment and never renders it or sends it in the address', () => {
    show()
    expect(screen.getByRole('heading', { name: 'Invitación a Oficina virtual' })).toBeInTheDocument()
    expect(document.location.search).toBe('')
    expect(document.location.hash).toBe('')
    expect(document.body).not.toHaveTextContent('opaque-bearer')
  })

  it('requires a chosen password for a new account and posts it only in the body', async () => {
    invitations.accept.mockResolvedValue({ data: { accepted: true } })
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Crear mi cuenta' }))
    fireEvent.change(screen.getByLabelText('Contraseña nueva'), { target: { value: 'una-clave-segura' } })
    fireEvent.change(screen.getByLabelText('Confirmar contraseña'), { target: { value: 'una-clave-segura' } })
    fireEvent.click(screen.getByRole('button', { name: 'Aceptar invitación' }))
    await waitFor(() => expect(invitations.accept).toHaveBeenCalledWith({ token: 'opaque-bearer', password: 'una-clave-segura' }, expect.objectContaining({ accessToken: null })))
    expect(screen.getByRole('status')).toHaveTextContent('La invitación fue aceptada.')
  })

  it('uses an existing signed-in target without asking it to replace a password', async () => {
    useJaxStore.setState({ token: 'session-token', user: { user_id: 8, email: 'ana@example.test' }, sessionRestoring: false, _sessionEpoch: 1 })
    invitations.accept.mockResolvedValue({ data: { accepted: true } })
    show()
    expect(screen.getByText('Vas a aceptar esta invitación con ana@example.test.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Contraseña nueva')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Aceptar invitación' }))
    await waitFor(() => expect(invitations.accept).toHaveBeenCalledWith({ token: 'opaque-bearer' }, expect.objectContaining({ accessToken: 'session-token' })))
  })

  it('waits for session restoration before choosing new-account or existing-account acceptance', async () => {
    useJaxStore.setState({ token: null, user: null, sessionRestoring: true, _sessionEpoch: 1 })
    show()
    expect(screen.getByRole('status')).toHaveTextContent('Comprobando tu sesión…')
    expect(screen.queryByRole('button', { name: 'Crear mi cuenta' })).not.toBeInTheDocument()
    await act(async () => useJaxStore.setState({ token: 'session-token', user: { user_id: 8, email: 'ana@example.test' }, sessionRestoring: false, _sessionEpoch: 2 }))
    expect(screen.getByText('Vas a aceptar esta invitación con ana@example.test.')).toBeInTheDocument()
  })

  it('does not apply a late acceptance result after the identity epoch changes', async () => {
    let resolve
    invitations.accept.mockReturnValue(new Promise((done) => { resolve = done }))
    useJaxStore.setState({ token: 'session-token', user: { user_id: 8, email: 'ana@example.test' }, sessionRestoring: false, _sessionEpoch: 1 })
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Aceptar invitación' }))
    await act(async () => useJaxStore.setState({ token: 'other-token', user: { user_id: 9, email: 'otra@example.test' }, sessionRestoring: false, _sessionEpoch: 2 }))
    await act(async () => resolve({ data: { accepted: true } }))
    await waitFor(() => expect(screen.getByText('Vas a aceptar esta invitación con otra@example.test.')).toBeInTheDocument())
    expect(screen.queryByText('La invitación fue aceptada.')).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Aceptar invitación' })).toBeEnabled()
  })

  it.each([
    ['corta', 'siete77', 'Mínimo 8 caracteres'],
    ['larga', 'ñ'.repeat(37), 'La contraseña es demasiado larga (máximo 72 bytes; los acentos ocupan 2).'],
  ])('rejects a %s new-account password with the shared password rule', async (_kind, password, message) => {
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Crear mi cuenta' }))
    fireEvent.change(screen.getByLabelText('Contraseña nueva'), { target: { value: password } })
    fireEvent.change(screen.getByLabelText('Confirmar contraseña'), { target: { value: password } })
    fireEvent.click(screen.getByRole('button', { name: 'Aceptar invitación' }))
    expect(await screen.findByText(message)).toBeInTheDocument()
    expect(invitations.accept).not.toHaveBeenCalled()
  })

  it('fails closed when the fragment has no token', () => {
    show('')
    expect(screen.getByRole('alert')).toHaveTextContent('El enlace de invitación no es válido.')
    expect(screen.queryByRole('button', { name: 'Aceptar invitación' })).not.toBeInTheDocument()
  })

  it('offers a neutral login path while retaining the bearer only in session memory', () => {
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sesión' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/login')
    expect(document.location.search).toBe('')
    expect(document.location.hash).toBe('')
    expect(window.sessionStorage.getItem('office-invitation-token-v1')).toBe('opaque-bearer')
  })

  it('handles the real existing-session HTTP detail without revealing account existence and sends the visitor to login', async () => {
    invitations.accept.mockRejectedValue({ response: { status: 409, data: { detail: 'office_invitation_existing_session_required' } } })
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Crear mi cuenta' }))
    fireEvent.change(screen.getByLabelText('Contraseña nueva'), { target: { value: 'una-clave-segura' } })
    fireEvent.change(screen.getByLabelText('Confirmar contraseña'), { target: { value: 'una-clave-segura' } })
    fireEvent.click(screen.getByRole('button', { name: 'Aceptar invitación' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Iniciá sesión para continuar con la invitación.')
    expect(screen.queryByText(/cuenta existente/i)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sesión' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/login')
  })

  it('treats a lost create-and-accept response as ambiguous and prevents a retry', async () => {
    invitations.accept.mockRejectedValue(new Error('network lost'))
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Crear mi cuenta' }))
    fireEvent.change(screen.getByLabelText('Contraseña nueva'), { target: { value: 'una-clave-segura' } })
    fireEvent.change(screen.getByLabelText('Confirmar contraseña'), { target: { value: 'una-clave-segura' } })
    fireEvent.click(screen.getByRole('button', { name: 'Aceptar invitación' }))
    expect(await screen.findByRole('status')).toHaveTextContent('No pudimos confirmar el resultado. Iniciá sesión para recuperar el acceso; no reenvíes la invitación.')
    expect(screen.queryByRole('button', { name: 'Aceptar invitación' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sesión' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/login')
  })
})
