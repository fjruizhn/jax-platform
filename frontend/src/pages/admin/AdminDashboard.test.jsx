import { render, screen, within } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'
import { readFileSync } from 'node:fs'

vi.mock('../../api/client', () => ({ default: { get: vi.fn() } }))

import api from '../../api/client'
import AdminDashboard from './AdminDashboard'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

const DATOS = {
  services: [
    { name: 'LAS MANOS', port: 7777, status: 'alive', latency_ms: 3 },
    { name: 'JAX Engine', port: null, status: 'sin_configurar', latency_ms: null },
  ],
  stats: {
    messages_today: 12, images_generated: 2, pipelines_completed: 5, users_active: 3,
    users_locked: 0, api_keys_configured: 4, api_keys_total: 5,
    ram: { total_mb: 1000, used_mb: 900, percent: 90 },
  },
}

beforeEach(() => {
  localStorage.clear()
  api.get.mockResolvedValue({ data: DATOS })
})

describe('AdminDashboard (frente A)', () => {
  it('un servicio sin configurar no se pinta como vivo y dice por qué', async () => {
    render(<I18nProvider><AdminDashboard /></I18nProvider>)
    const tarjeta = (await screen.findByText('JAX Engine')).closest('div.rounded-lg')
    expect(within(tarjeta).getByText(es.serviceNotConfigured)).toBeInTheDocument()
    expect(tarjeta.className).not.toContain('border-exito-borde')
  })

  it('el tono llega como clase completa, sin armar clases en runtime', () => {
    // Indirección vía `base`: escrito junto a `import.meta.url`, Vite lo
    // detecta como asset y lo reescribe a una URL http://localhost del
    // servidor de dev (entorno jsdom) -- readFileSync exige file://.
    const base = import.meta.url
    const fuente = readFileSync(new URL('./AdminDashboard.jsx', base), 'utf8')
    expect(fuente).not.toMatch(/const colors\s*=/)
    expect(fuente).not.toContain('label="RAM"')
    expect(fuente).toMatch(/tono="text-info"/)
  })

  it('la etiqueta de RAM sale de i18n', async () => {
    localStorage.setItem('jax_lang', 'en')
    render(<I18nProvider><AdminDashboard /></I18nProvider>)
    expect(await screen.findByText(en.statRam)).toBeInTheDocument()
    expect(typeof es.statRam).toBe('string')
  })

  it('pipelines completados muestra el número del backend', async () => {
    render(<I18nProvider><AdminDashboard /></I18nProvider>)
    const etiqueta = await screen.findByText(es.statPipelines)
    expect(within(etiqueta.parentElement).getByText('5')).toBeInTheDocument()
  })

  it.each([
    [0, 0, 'text-aviso'],
    [4, 5, 'text-aviso'],
    [5, 5, 'text-exito'],
  ])('llaves %i/%i se pintan %s (0/0 no es verde)', async (configuradas, total, tono) => {
    api.get.mockResolvedValue({ data: { ...DATOS, stats: {
      ...DATOS.stats, api_keys_configured: configuradas, api_keys_total: total } } })
    render(<I18nProvider><AdminDashboard /></I18nProvider>)
    const etiqueta = await screen.findByText(es.statApiKeysLabel)
    expect(within(etiqueta.parentElement).getByText(`${configuradas}/${total}`).className).toContain(tono)
  })
})
