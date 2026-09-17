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
import { useTema } from '../../store/useTema'
import { aplicarTema } from '../../tema/aplicarTema'

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
  useTema.setState({ theme: 'dark', predeterminado: null })
  aplicarTema('dark')
})

describe('AdminSettings -- el botón mientras guarda (ronda final M8)', () => {
  it('dice "Guardando…", no el texto de subir adjuntos', async () => {
    api.get.mockResolvedValue(CONFIG)
    let soltar
    api.put.mockReturnValue(new Promise((resolve) => { soltar = resolve }))
    renderSettings()
    await guardar()
    expect(await screen.findByRole('button', { name: es.adminBindingsSaving })).toBeDisabled()
    expect(screen.queryByText(es.attachUploading)).not.toBeInTheDocument()
    soltar({ data: {} })
  })
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

  it('si la carga falla, Guardar queda deshabilitado: no se "guarda" una pantalla vacía', async () => {
    api.get.mockRejectedValue(rechazo(500, 'lo_que_sea'))
    renderSettings()
    await screen.findByRole('alert')
    expect(screen.getByRole('button', { name: es.adminSettingsSave })).toBeDisabled()
  })

  it('tras un guardado bueno, un fallo no deja el botón diciendo Guardado', async () => {
    api.get.mockResolvedValue(CONFIG)
    api.put.mockResolvedValueOnce({ data: { ok: true } }).mockRejectedValueOnce(rechazo(400, 'config_clave_reservada'))
    renderSettings()
    await guardar()
    const guardado = await screen.findByRole('button', { name: `✓ ${es.adminSettingsSaved}` })
    fireEvent.click(guardado)
    await screen.findByRole('alert')
    expect(screen.getByRole('button', { name: es.adminSettingsSave })).toBeInTheDocument()
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

  it('guardar el tema predeterminado lo aplica en este navegador si el usuario no eligió', async () => {
    api.get.mockResolvedValue({ data: { config: [
      { key: 'system_name', value: 'Axioma' },
      { key: 'theme_default', value: 'light' },
    ] } })
    api.put.mockResolvedValue({ data: { ok: true } })
    renderSettings()
    await guardar()
    await waitFor(() => expect(localStorage.getItem('jax_theme_default')).toBe('light'))
    expect(document.documentElement.getAttribute('data-tema')).toBe('claro')
  })

  // Decisión de Fernando (2026-09-14, brief fix-vivo-brief.md §B): guardar el
  // predeterminado en Configuración también fija la elección del propio
  // admin -- su navegador cambia de tema aunque tuviera otra elección.
  it('guardar el predeterminado fija también la elección del admin, aunque tuviera otra', async () => {
    localStorage.setItem('jax_theme', 'dark')
    api.get.mockResolvedValue({ data: { config: [
      { key: 'system_name', value: 'Axioma' },
      { key: 'theme_default', value: 'light' },
    ] } })
    api.put.mockResolvedValue({ data: { ok: true } })
    renderSettings()
    await guardar()
    await waitFor(() => expect(useTema.getState().theme).toBe('light'))
    expect(localStorage.getItem('jax_theme')).toBe('light')
    expect(localStorage.getItem('jax_theme_default')).toBe('light')
    expect(document.documentElement.getAttribute('data-tema')).toBe('claro')
  })

  it('un PUT que falla no cambia el tema ni la elección', async () => {
    localStorage.setItem('jax_theme', 'dark')
    api.get.mockResolvedValue({ data: { config: [
      { key: 'system_name', value: 'Axioma' },
      { key: 'theme_default', value: 'light' },
    ] } })
    api.put.mockRejectedValue(rechazo(400, 'config_clave_reservada'))
    renderSettings()
    await guardar()
    await screen.findByRole('alert')
    expect(localStorage.getItem('jax_theme')).toBe('dark')
    expect(localStorage.getItem('jax_theme_default')).toBeNull()
    expect(useTema.getState().theme).toBe('dark')
  })
})
