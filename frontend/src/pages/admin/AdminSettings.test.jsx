import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Pantalla "Configuración" (DEUDA.md, anotados de la etapa 1, 2026-09-13):
// el PUT /admin/config rechaza con un código (`config_clave_reservada`,
// `config_collation_desconocida`) y la pantalla lo tragaba con un catch vacío:
// quien intentaba guardar no veía por qué no se guardó. Cada código se traduce;
// uno que la pantalla no conoce cae en un texto genérico, nunca en silencio.
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), put: vi.fn() } }))

import api from '../../api/client'
import AdminSettings from './AdminSettings'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

const CONFIG = { data: { config: [{ key: 'system_name', value: 'Axioma' }] } }

function rechazo(status, detail) {
  return { response: { status, data: { detail } } }
}

function renderSettings() {
  return render(<I18nProvider><AdminSettings /></I18nProvider>)
}

async function guardar() {
  await waitFor(() => expect(screen.getByDisplayValue('Axioma')).toBeInTheDocument())
  fireEvent.click(screen.getByRole('button', { name: es.adminSettingsSave }))
}

beforeEach(() => {
  api.get.mockReset(); api.put.mockReset()
  localStorage.clear()
})

describe('AdminSettings -- los errores del guardado se ven', () => {
  it('los textos existen en los dos idiomas', () => {
    for (const clave of ['config_clave_reservada', 'config_collation_desconocida', 'adminSettingsSaveError', 'adminSettingsLoadError']) {
      expect(es[clave], `es.${clave}`).toBeTruthy()
      expect(en[clave], `en.${clave}`).toBeTruthy()
    }
  })

  it('una clave reservada se nombra', async () => {
    api.get.mockResolvedValue(CONFIG)
    api.put.mockRejectedValue(rechazo(400, 'config_clave_reservada'))
    renderSettings()
    await guardar()
    expect(await screen.findByRole('alert')).toHaveTextContent(es.config_clave_reservada)
  })

  it('una collation desconocida se nombra', async () => {
    api.get.mockResolvedValue(CONFIG)
    api.put.mockRejectedValue(rechazo(503, 'config_collation_desconocida'))
    renderSettings()
    await guardar()
    expect(await screen.findByRole('alert')).toHaveTextContent(es.config_collation_desconocida)
  })

  it('un código desconocido cae en el texto genérico, no en silencio', async () => {
    api.get.mockResolvedValue(CONFIG)
    api.put.mockRejectedValue(rechazo(500, 'algo_que_la_pantalla_no_conoce'))
    renderSettings()
    await guardar()
    expect(await screen.findByRole('alert')).toHaveTextContent(es.adminSettingsSaveError)
  })

  it('si la carga falla, se dice', async () => {
    api.get.mockRejectedValue(rechazo(500, 'lo_que_sea'))
    renderSettings()
    expect(await screen.findByRole('alert')).toHaveTextContent(es.adminSettingsLoadError)
  })

  it('un guardado bueno no deja ninguna alerta', async () => {
    api.get.mockResolvedValue(CONFIG)
    api.put.mockResolvedValue({ data: { ok: true } })
    renderSettings()
    await guardar()
    await waitFor(() => expect(api.put).toHaveBeenCalled())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('un error viejo se borra al volver a guardar bien', async () => {
    api.get.mockResolvedValue(CONFIG)
    api.put.mockRejectedValueOnce(rechazo(400, 'config_clave_reservada')).mockResolvedValueOnce({ data: { ok: true } })
    renderSettings()
    await guardar()
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: es.adminSettingsSave }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })
})
