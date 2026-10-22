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
    user_id: 7, email: 'persona@example.test', account_class: 'OFFICE_INTERNAL',
  } })
})

describe('Virtual Office', () => {
  it('identifies the signed-in restricted account without inferring administrative authority', () => {
    show()
    expect(screen.getByRole('heading', { name: 'Oficina virtual' })).toBeInTheDocument()
    expect(screen.getByText('persona@example.test')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('La administración de proyectos y permisos se habilitará cuando el servidor confirme una capacidad de Oficina.')
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Otorgar permiso|Invitar persona|Elegir proyecto/ })).not.toBeInTheDocument()
  })

  it('opens access information in the shared keyboard-accessible dialog', () => {
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Información de acceso' }))
    expect(screen.getByRole('dialog', { name: 'Acceso de Oficina virtual' })).toBeInTheDocument()
  })
})
