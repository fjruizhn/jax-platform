import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { MemoryRouter, useLocation } from 'react-router-dom'

const api = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }))
vi.mock('../api/client', () => ({ default: api }))

import { useJaxStore } from '../store/useJaxStore'
import Login from './Login'
import { I18nProvider } from '../i18n/index.jsx'

const initial = useJaxStore.getState()
function Location() { return <output data-testid="location">{useLocation().pathname}</output> }
function show() { return render(<I18nProvider><MemoryRouter initialEntries={['/login']}><Login /><Location /></MemoryRouter></I18nProvider>) }
function submit() {
  fireEvent.change(screen.getByPlaceholderText('nombre@empresa.com'), { target: { value: 'a@b.c' } })
  fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'password' } })
  fireEvent.click(screen.getByRole('button', { name: /Entrar/i }))
}
beforeEach(() => { useJaxStore.setState(initial, true); api.post.mockReset(); api.get.mockReset() })

describe('Login with the real Zustand store', () => {
  it('establishes a Classic session and navigates to the Classic root after its own epoch bump', async () => {
    api.post.mockResolvedValue({ data: { access_token: 'classic', user_id: 1, email: 'a@b.c', account_class: 'INTERNAL' } })
    show(); submit()
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/'))
    expect(useJaxStore.getState().token).toBe('classic')
  })
  it('routes an Office identity to account security after its own epoch bump', async () => {
    api.post.mockResolvedValue({ data: { access_token: 'office', user_id: 1, email: 'a@b.c', account_class: 'OFFICE_INTERNAL' } })
    show(); submit()
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/account/security'))
    expect(useJaxStore.getState().token).toBe('office')
  })
  it('does not navigate a stale A response after B has established a newer session', async () => {
    let resolveA
    api.post.mockReturnValueOnce(new Promise((resolve) => { resolveA = resolve }))
    show(); submit()
    useJaxStore.getState().establecerSesion({ access_token: 'b', user_id: 2, account_class: 'INTERNAL' })
    resolveA({ data: { access_token: 'a', user_id: 1, account_class: 'INTERNAL' } })
    await waitFor(() => expect(useJaxStore.getState().token).toBe('b'))
    expect(screen.getByTestId('location')).toHaveTextContent('/login')
  })
})
