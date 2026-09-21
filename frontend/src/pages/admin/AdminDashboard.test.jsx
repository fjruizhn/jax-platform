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
    messages_today: 12, images_generated: 2, facts_unverified: 7, pipelines_completed: 5,
    users_active: 3, users_locked: 0, api_keys_configured: 4, api_keys_total: 5,
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

// A-5x (2026-09-20): "Mensajes" contaba TODAS las peticiones (COUNT(*) sobre
// axioma_usage del día en SQL_USO_DEL_DIA, backend/api/admin/dashboard.py) --
// las imágenes iban DENTRO de ese número y otra vez en su propia tarjeta:
// doble conteo, y la etiqueta mentía sobre qué medía. La consulta no cambió
// (el número estaba bien); lo que cambia es que "Mensajes" pasa a decir
// "Peticiones hoy" y las imágenes se ven como SUBCONJUNTO (prop `sub` de
// StatCard, no una tarjeta hermana nueva).
describe('AdminDashboard — peticiones e imágenes (A-5x)', () => {
  it('"Peticiones hoy" muestra el total y las imágenes como subconjunto, sin tarjeta propia', async () => {
    render(<I18nProvider><AdminDashboard /></I18nProvider>)
    const etiqueta = await screen.findByText(es.statRequestsToday)
    const tarjeta = etiqueta.parentElement
    expect(within(tarjeta).getByText('12')).toBeInTheDocument()
    expect(within(tarjeta).getByText(es.statImagesSub(2))).toBeInTheDocument()
    // Ya no existe una tarjeta hermana rotulada "Imágenes": el único lugar
    // donde aparece el número de imágenes es el subconjunto de arriba. Las
    // claves viejas (statMessages/statImages) se retiraron de i18n -- se
    // busca el texto literal, no la clave (que ya no existe).
    expect(screen.queryByText('Imágenes')).not.toBeInTheDocument()
  })

  it('la etiqueta vieja "Mensajes" ya no se usa', async () => {
    render(<I18nProvider><AdminDashboard /></I18nProvider>)
    await screen.findByText(es.statRequestsToday)
    expect(screen.queryByText('Mensajes')).not.toBeInTheDocument()
  })
})

// Restricción dura (2026-09-20): "sin verificar" tiene que usar el MISMO
// filtro que la pantalla de Memoria (backend, is_verified=0 AND
// superseded_by IS NULL AND (expires_at IS NULL OR expires_at > NOW())) --
// acá sólo se verifica que el frontend RENDERICE el número que manda el
// backend, siempre visible, incluso en 0 (a diferencia de "Bloqueados").
describe('AdminDashboard — «Sin verificar» (restricción dura)', () => {
  it.each([0, 7])('se muestra siempre, incluso en 0 (a diferencia de «Bloqueados»)', async (n) => {
    api.get.mockResolvedValue({ data: { ...DATOS, stats: { ...DATOS.stats, facts_unverified: n } } })
    render(<I18nProvider><AdminDashboard /></I18nProvider>)
    const etiqueta = await screen.findByText(es.statFactsUnverified)
    expect(within(etiqueta.parentElement).getByText(String(n))).toBeInTheDocument()
  })

  it.each([
    [0, 'text-exito'],
    [7, 'text-aviso'],
  ])('en %i se pinta %s -- el 0 es la señal de estar al día', async (n, tono) => {
    api.get.mockResolvedValue({ data: { ...DATOS, stats: { ...DATOS.stats, facts_unverified: n } } })
    render(<I18nProvider><AdminDashboard /></I18nProvider>)
    const etiqueta = await screen.findByText(es.statFactsUnverified)
    expect(within(etiqueta.parentElement).getByText(String(n)).className).toContain(tono)
  })
})
