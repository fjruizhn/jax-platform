import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import '@testing-library/jest-dom'
import EditarUsuarioModal from './EditarUsuarioModal'
import { I18nProvider } from '../../i18n/index.jsx'

const USUARIO = { user_id: 2, email: 'b@x.io', role: 'superadmin', status: 'active' }

function renderModal(onGuardar = vi.fn().mockResolvedValue()) {
  render(<I18nProvider><EditarUsuarioModal usuario={USUARIO} onGuardar={onGuardar} onCerrar={vi.fn()} /></I18nProvider>)
  return onGuardar
}

describe('EditarUsuarioModal', () => {
  it('envía solo lo que cambió', () => {
    const onGuardar = renderModal()
    fireEvent.change(screen.getByLabelText('Rol'), { target: { value: 'operator' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }))
    expect(onGuardar).toHaveBeenCalledWith({ role: 'operator' })
  })

  it('el estado no ofrece "deleted" (eso es la baja) y sin cambios no se puede guardar', () => {
    renderModal()
    const opciones = [...screen.getByLabelText('Estado').querySelectorAll('option')].map((o) => o.value)
    expect(opciones).toEqual(['active', 'inactive'])
    expect(screen.getByRole('button', { name: 'Guardar' })).toBeDisabled()
  })
})

// M-2 (revisión final PR 2, 2026-09-14; mudado acá el 2026-09-15, etapa 3,
// Ruling U6): el rol ya no se cambia con un select en la fila de la tabla sino
// en este modal. La guarda sigue: border-borde (1,41:1) queda bajo el mínimo
// 3:1 de WCAG 1.4.11 para un control, y focus:outline-none necesita un
// indicador de foco de reemplazo.
describe('EditarUsuarioModal -- borde y foco del select de rol (M-2)', () => {
  it('usa border-borde-control (par de 3:1 declarado) y focus:border-foco', () => {
    renderModal()
    const select = screen.getByLabelText('Rol')
    expect(select.className).toMatch(/(^|\s)border-borde-control(\s|$)/)
    expect(select.className).not.toMatch(/(^|\s)border-borde(\s|$)/)
    expect(select.className).toMatch(/(^|\s)focus:border-foco(\s|$)/)
  })
})
