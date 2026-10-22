import { beforeEach, describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom'
import { MemoryRouter } from 'react-router-dom'
import { I18nProvider } from '../i18n/index.jsx'
import { useJaxStore } from '../store/useJaxStore'
import VirtualOffice from './VirtualOffice'

function show() {
  return render(<I18nProvider><MemoryRouter><VirtualOffice /></MemoryRouter></I18nProvider>)
}

beforeEach(() => {
  useJaxStore.setState({ token: 'office-token', user: {
    user_id: 7, email: 'persona@example.test', account_class: 'OFFICE_INTERNAL', office_role: 'GENERAL_ADMIN',
  } })
})

describe('Virtual Office', () => {
  it('identifies the signed-in office account and starts in the protected project selector', () => {
    show()
    expect(screen.getByRole('heading', { name: 'Oficina virtual' })).toBeInTheDocument()
    expect(screen.getByText('persona@example.test')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Proyectos' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('status')).toHaveTextContent('La conexión con proyectos de Oficina todavía no está disponible.')
  })

  it('uses keyboard-accessible tabs and keeps unavailable mutations disabled until the Office API exists', () => {
    show()
    const people = screen.getByRole('tab', { name: 'Personas' })
    people.focus()
    fireEvent.keyDown(people, { key: 'ArrowRight' })
    expect(screen.getByRole('tab', { name: 'Permisos' })).toHaveFocus()
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Permisos' }), { key: 'Enter' })
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Los grants se habilitarán cuando la API de Oficina publique su contrato.')
    expect(screen.getByRole('button', { name: 'Otorgar permiso' })).toBeDisabled()
  })
})
