import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import DialogoProgramacionSync from './DialogoProgramacionSync'
import { I18nProvider } from '../../i18n/index.jsx'
import api from '../../api/client'

vi.mock('../../api/client', () => ({ default: { put: vi.fn() } }))

const CONFIG_BASE = {
  habilitado: true,
  cada_valor: 6,
  cada_unidad: 'horas',
  actualizado_por: null,
  actualizado_en: null,
  proxima_corrida_estimada: '2026-09-27T12:00:00',
}

function renderDialogo(props = {}) {
  return render(
    <I18nProvider>
      <DialogoProgramacionSync
        config={CONFIG_BASE}
        onGuardado={vi.fn()}
        onCerrar={vi.fn()}
        {...props}
      />
    </I18nProvider>,
  )
}

beforeEach(() => {
  vi.resetAllMocks()
})

describe('DialogoProgramacionSync', () => {
  it('muestra el interruptor, el valor y la unidad actuales', () => {
    renderDialogo()
    expect(screen.getByRole('checkbox', { name: /automática/i })).toBeChecked()
    expect(screen.getByDisplayValue('6')).toBeInTheDocument()
    expect(screen.getByRole('combobox')).toHaveValue('horas')
  })

  it('muestra la próxima corrida estimada cuando hay una', () => {
    renderDialogo()
    expect(screen.getByText(/Próxima corrida estimada/)).toBeInTheDocument()
  })

  it('sin próxima corrida estimada, muestra el aviso de que se calcula después', () => {
    renderDialogo({ config: { ...CONFIG_BASE, proxima_corrida_estimada: null } })
    expect(screen.getByText(/se calcula después/i)).toBeInTheDocument()
  })

  it('apagado, muestra el aviso de que está apagado en vez de la próxima corrida', () => {
    renderDialogo({ config: { ...CONFIG_BASE, habilitado: false, proxima_corrida_estimada: null } })
    expect(screen.getByText(/está apagada/i)).toBeInTheDocument()
  })

  it('Guardar hace PUT con los valores editados y llama onGuardado', async () => {
    api.put.mockResolvedValue({ data: { ...CONFIG_BASE, cada_valor: 3, cada_unidad: 'dias' } })
    const onGuardado = vi.fn()
    renderDialogo({ onGuardado })

    fireEvent.change(screen.getByDisplayValue('6'), { target: { value: '3' } })
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'dias' } })
    fireEvent.click(screen.getByRole('button', { name: /guardar/i }))

    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/admin/models/sync/config', {
      habilitado: true, cada_valor: 3, cada_unidad: 'dias',
    }))
    await waitFor(() => expect(onGuardado).toHaveBeenCalled())
  })

  it('Cancelar llama onCerrar sin guardar', () => {
    const onCerrar = vi.fn()
    renderDialogo({ onCerrar })

    fireEvent.click(screen.getByRole('button', { name: /cancelar/i }))

    expect(onCerrar).toHaveBeenCalled()
    expect(api.put).not.toHaveBeenCalled()
  })

  it('Escape cierra el diálogo', () => {
    const onCerrar = vi.fn()
    renderDialogo({ onCerrar })

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(onCerrar).toHaveBeenCalled()
  })

  it('un 422 de config inválida se traduce y se muestra', async () => {
    api.put.mockRejectedValue({
      response: { data: { detail: { code: 'catalogo_sync_config_invalida', campo: 'cada_valor', message: 'x' } } },
    })
    renderDialogo()

    fireEvent.click(screen.getByRole('button', { name: /guardar/i }))

    expect(await screen.findByText(/valor inválido/i)).toBeInTheDocument()
  })

  it('MINOR-2 (tercera ronda de la auditoría adversarial, 2026-09-27): valida=false muestra el aviso de config corrupta', () => {
    renderDialogo({ config: { ...CONFIG_BASE, cada_valor: 999, valida: false } })
    expect(screen.getByText(/no es válida/i)).toBeInTheDocument()
  })

  it('sin la marca "valida" (config normal, GET viejo) no muestra el aviso de config corrupta', () => {
    renderDialogo()
    expect(screen.queryByText(/no es válida/i)).not.toBeInTheDocument()
  })

  it('valida=true no muestra el aviso de config corrupta', () => {
    renderDialogo({ config: { ...CONFIG_BASE, valida: true } })
    expect(screen.queryByText(/no es válida/i)).not.toBeInTheDocument()
  })

  it('MINOR-3 (cuarta ronda de la auditoría adversarial, 2026-09-27): con cada_unidad ' +
     'corrupta el <select> NO cae en "horas" en silencio -- arranca vacío y exige elegir', () => {
    // El defecto real: con cada_unidad="lunas" (fuera de UNIDADES), un
    // <select> controlado con ese value cae al PRIMER <option> ("horas") en
    // pantalla -- pero el estado interno seguía siendo "lunas". Parecía una
    // config válida en horas, lista para guardar tal cual, sin serlo.
    renderDialogo({ config: { ...CONFIG_BASE, cada_unidad: 'lunas', valida: false } })

    const select = screen.getByRole('combobox')
    expect(select).toHaveValue('')  // NUNCA "horas" por default del navegador
    expect(screen.getByText(/elegí una unidad/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /guardar/i })).toBeDisabled()
  })

  it('MINOR-3: elegir una unidad válida Y un valor habilita Guardar', () => {
    // Los dos arrancan vacíos con la config inválida (ver el test de arriba)
    // -- hace falta completar los dos, no sólo la unidad, para habilitar.
    renderDialogo({ config: { ...CONFIG_BASE, cada_unidad: 'lunas', valida: false } })

    expect(screen.getByRole('button', { name: /guardar/i })).toBeDisabled()

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'dias' } })
    expect(screen.getByRole('button', { name: /guardar/i })).toBeDisabled()  // falta el valor

    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '3' } })
    expect(screen.getByRole('button', { name: /guardar/i })).not.toBeDisabled()
  })

  it('MINOR-3: con la config válida, el <select> arranca con la unidad real, no vacío', () => {
    renderDialogo()
    expect(screen.getByRole('combobox')).toHaveValue('horas')
    expect(screen.queryByText(/elegí una unidad/i)).not.toBeInTheDocument()
  })

  it('desmarcar el interruptor apaga la sincronización automática al guardar', async () => {
    api.put.mockResolvedValue({ data: { ...CONFIG_BASE, habilitado: false } })
    renderDialogo()

    fireEvent.click(screen.getByRole('checkbox', { name: /automática/i }))
    fireEvent.click(screen.getByRole('button', { name: /guardar/i }))

    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/admin/models/sync/config', {
      habilitado: false, cada_valor: 6, cada_unidad: 'horas',
    }))
  })
})
