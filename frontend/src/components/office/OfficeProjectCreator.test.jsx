// OP 358 pieza 4: el creador ofrece la sala (vertical) al crear, preselección
// incluida, y la firma de idempotencia incluye la sala elegida — cambiarla es
// otra intención, con otra llave. Sin registro no hay selector, y la llave de
// preselección que no está en el registro se ignora.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { I18nProvider } from '../../i18n/index.jsx'
import OfficeProjectCreator from './OfficeProjectCreator'

const projects = vi.hoisted(() => ({ create: vi.fn() }))
vi.mock('../../api/virtualOffice', () => ({ officeProjectsApi: projects }))

const VERTICALES = [
  { id: 'agencia', estado: 'activa' },
  { id: 'banca', estado: 'proximamente' },
]

function show(props = {}) {
  return render(<I18nProvider><OfficeProjectCreator tenantId={4} actorUserId={7} verticales={VERTICALES} onCreated={vi.fn()} onForbidden={vi.fn()} {...props} /></I18nProvider>)
}

async function submit(name = 'Nuevo proyecto') {
  fireEvent.click(screen.getByRole('button', { name: 'Crear proyecto' }))
  fireEvent.change(screen.getByLabelText('Nombre del proyecto'), { target: { value: name } })
  fireEvent.click(screen.getAllByRole('button', { name: 'Crear proyecto' })[1])
}

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  projects.create.mockResolvedValue({ data: { id: 31, created: true } })
})

describe('Creador de proyectos de Oficina', () => {
  it('preselecciona la sala pedida y firma la creación con ella', async () => {
    const onCreated = vi.fn()
    show({ verticalInicial: 'agencia', onCreated })
    fireEvent.click(screen.getByRole('button', { name: 'Crear proyecto' }))
    expect(screen.getByLabelText('Sala de la oficina (opcional)')).toHaveValue('agencia')
    fireEvent.change(screen.getByLabelText('Nombre del proyecto'), { target: { value: 'Nuevo proyecto' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Crear proyecto' })[1])
    await waitFor(() => expect(projects.create).toHaveBeenCalledWith(
      { tenant_id: 4, name: 'Nuevo proyecto', description: null, vertical: 'agencia' },
      expect.any(String),
    ))
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(31))
  })

  it('la sala General no viaja en el payload', async () => {
    show()
    await submit()
    await waitFor(() => expect(projects.create).toHaveBeenCalledTimes(1))
    const [payload] = projects.create.mock.calls[0]
    expect(payload).toEqual({ tenant_id: 4, name: 'Nuevo proyecto', description: null })
    expect(payload).not.toHaveProperty('vertical')
  })

  it('sin registro de salas no hay selector y la creación sigue igual', async () => {
    show({ verticales: [] })
    fireEvent.click(screen.getByRole('button', { name: 'Crear proyecto' }))
    expect(screen.queryByLabelText('Sala de la oficina (opcional)')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Nombre del proyecto'), { target: { value: 'Nuevo proyecto' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Crear proyecto' })[1])
    await waitFor(() => expect(projects.create).toHaveBeenCalledWith(
      { tenant_id: 4, name: 'Nuevo proyecto', description: null },
      expect.any(String),
    ))
  })

  it('ignora una preselección que no está en el registro', async () => {
    show({ verticales: [{ id: 'banca', estado: 'proximamente' }], verticalInicial: 'agencia' })
    fireEvent.click(screen.getByRole('button', { name: 'Crear proyecto' }))
    expect(screen.getByLabelText('Sala de la oficina (opcional)')).toHaveValue('')
    fireEvent.change(screen.getByLabelText('Sala de la oficina (opcional)'), { target: { value: 'banca' } })
    fireEvent.change(screen.getByLabelText('Nombre del proyecto'), { target: { value: 'Nuevo proyecto' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Crear proyecto' })[1])
    await waitFor(() => expect(projects.create).toHaveBeenCalledWith(
      { tenant_id: 4, name: 'Nuevo proyecto', description: null, vertical: 'banca' },
      expect.any(String),
    ))
  })
})
