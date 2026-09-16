import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// Re-revisión acotada (2026-09-15, Fernando: sin diferidos): AdminCosts.jsx
// formateaba tokens_in/tokens_out con toLocaleString() sin locale -- seguía
// el locale del NAVEGADOR, no el idioma activo de la app (mismo defecto que
// I-1 arreglaba en las fechas de AdminRepository/AdminUsers, pero acá con
// números). Se usa el mismo localeFor(lang) del contexto i18n.
//
// Nota de verificación (Principio I, "el que supone se equivoca"): medido
// con este Node/ICU, es-HN y en-US dan el MISMO agrupamiento de miles para
// enteros (Honduras usa la misma convención que EE.UU.: coma de millar,
// punto decimal -- distinto de es-ES/es genérico, que usan punto). Por eso
// esta prueba no puede comparar el TEXTO renderizado entre idiomas (sería
// igual en los dos); en su lugar espía Number.prototype.toLocaleString y
// verifica el argumento de locale que realmente recibe, que es lo que
// localeFor(lang) cambia.
vi.mock('../../api/client', () => ({ default: { get: vi.fn() } }))

import api from '../../api/client'
import AdminCosts from './AdminCosts'
import { I18nProvider } from '../../i18n/index.jsx'

const FILA = {
  facet: 'hyde', model: 'gpt-x', tokens_in: 1234567, tokens_out: 890123,
  cost_usd: 1.234567, unpriced_requests: 0, requests: 42,
}
const DATA = { by_facet: [FILA], chart_data: null }

function renderCosts() {
  return render(<I18nProvider><AdminCosts /></I18nProvider>)
}

beforeEach(() => {
  api.get.mockReset()
  api.get.mockResolvedValue({ data: DATA })
  localStorage.clear()
})

describe('AdminCosts -- locale de números (re-revisión, sin diferidos)', () => {
  it('en inglés, tokens_in/tokens_out se formatean con en-US, no con el locale del navegador', async () => {
    const spy = vi.spyOn(Number.prototype, 'toLocaleString')
    localStorage.setItem('jax_lang', 'en')
    renderCosts()
    await screen.findByText('gpt-x')
    expect(spy).toHaveBeenCalledWith('en-US')
    spy.mockRestore()
  })

  it('en español (por defecto), tokens_in/tokens_out se formatean con es-HN', async () => {
    const spy = vi.spyOn(Number.prototype, 'toLocaleString')
    renderCosts()
    await screen.findByText('gpt-x')
    expect(spy).toHaveBeenCalledWith('es-HN')
    spy.mockRestore()
  })

  it('el valor formateado sale correcto (1.234.567 con agrupamiento, cualquiera sea el locale)', async () => {
    renderCosts()
    expect(await screen.findByText('1,234,567')).toBeInTheDocument()
    expect(await screen.findByText('890,123')).toBeInTheDocument()
  })
})

// Task 7 (2026-09-15): GET /api/admin/usage expone registros_perdidos (filas
// que record_usage no pudo escribir desde el arranque del proceso). Con > 0
// el total esta incompleto y la vista lo tiene que decir.
describe('AdminCosts -- registros perdidos (Task 7)', () => {
  it('con registros_perdidos > 0 avisa "total incompleto: N registros perdidos"', async () => {
    api.get.mockResolvedValue({ data: { ...DATA, registros_perdidos: 3 } })
    renderCosts()
    expect(await screen.findByText('total incompleto: 3 registros perdidos')).toBeInTheDocument()
  })

  it('en inglés el aviso sale traducido con el número interpolado', async () => {
    localStorage.setItem('jax_lang', 'en')
    api.get.mockResolvedValue({ data: { ...DATA, registros_perdidos: 7 } })
    renderCosts()
    expect(await screen.findByText('incomplete total: 7 records lost')).toBeInTheDocument()
  })

  it('con registros_perdidos = 0 no hay aviso', async () => {
    api.get.mockResolvedValue({ data: { ...DATA, registros_perdidos: 0 } })
    renderCosts()
    await screen.findByText('gpt-x')
    expect(screen.queryByText(/registros perdidos/)).not.toBeInTheDocument()
  })

  it('sin el campo (backend viejo) no hay aviso', async () => {
    renderCosts()
    await screen.findByText('gpt-x')
    expect(screen.queryByText(/registros perdidos/)).not.toBeInTheDocument()
  })

  it('el aviso sale aunque no haya filas: si se perdieron todas, "sin datos" mentiría solo', async () => {
    api.get.mockResolvedValue({ data: { by_facet: [], chart_data: null, registros_perdidos: 2 } })
    renderCosts()
    expect(await screen.findByText('total incompleto: 2 registros perdidos')).toBeInTheDocument()
  })
})

// Task 4a (2026-09-15, plan cola-durable-uso): la pantalla tiene que
// distinguir DOS estados que antes eran uno solo (spec: docstring de
// registros_perdidos_stats en backend/api/admin/usage.py):
//
//  - PENDIENTE (`en_cola > 0`): el total esta incompleto pero se va a
//    completar solo cuando drene el reintento. Aviso SUAVE.
//  - PERDIDO (`registros_perdidos > 0` o `perdidas_por_desborde > 0`): el
//    total NUNCA se va a completar. Aviso FUERTE.
//
// Y los dos se pueden dar a la vez, en cuyo caso salen los dos. Mezclarlos
// (lo de antes) le dice "total incompleto" al admin cuando el total se
// arregla solo, o lo tranquiliza cuando no.
describe('AdminCosts -- pendientes vs perdidos (Task 4a)', () => {
  it('estado 1/4: sin nada en cola ni perdido, no sale ningun aviso', async () => {
    api.get.mockResolvedValue({
      data: { ...DATA, registros_perdidos: 0, en_cola: 0, perdidas_por_desborde: 0 },
    })
    renderCosts()
    await screen.findByText('gpt-x')
    expect(screen.queryByText(/registros perdidos/)).not.toBeInTheDocument()
    expect(screen.queryByText(/esperando reintento/)).not.toBeInTheDocument()
    expect(screen.queryByText(/respaldo/)).not.toBeInTheDocument()
  })

  it('estado 2/4: solo pendientes -- aviso suave, sin "total incompleto"', async () => {
    api.get.mockResolvedValue({
      data: { ...DATA, registros_perdidos: 0, en_cola: 5, perdidas_por_desborde: 0 },
    })
    renderCosts()
    expect(
      await screen.findByText('Hay 5 registros esperando reintento; el total va a completarse solo.')
    ).toBeInTheDocument()
    expect(screen.queryByText(/total incompleto/)).not.toBeInTheDocument()
  })

  it('estado 3/4: solo perdidos -- aviso fuerte, sin el de pendientes', async () => {
    api.get.mockResolvedValue({
      data: { ...DATA, registros_perdidos: 3, en_cola: 0, perdidas_por_desborde: 0 },
    })
    renderCosts()
    expect(await screen.findByText('total incompleto: 3 registros perdidos')).toBeInTheDocument()
    expect(screen.queryByText(/esperando reintento/)).not.toBeInTheDocument()
  })

  it('estado 4/4: los dos a la vez -- salen los dos avisos', async () => {
    api.get.mockResolvedValue({
      data: { ...DATA, registros_perdidos: 3, en_cola: 5, perdidas_por_desborde: 0 },
    })
    renderCosts()
    expect(await screen.findByText('total incompleto: 3 registros perdidos')).toBeInTheDocument()
    expect(
      screen.getByText('Hay 5 registros esperando reintento; el total va a completarse solo.')
    ).toBeInTheDocument()
  })

  it('el desborde del respaldo es perdida de verdad, pero se nombra distinto de "la DB rechazo la fila"', async () => {
    api.get.mockResolvedValue({
      data: { ...DATA, registros_perdidos: 0, en_cola: 0, perdidas_por_desborde: 4 },
    })
    renderCosts()
    expect(
      await screen.findByText('total incompleto: se llenó el respaldo y se descartaron 4 registros viejos')
    ).toBeInTheDocument()
    expect(screen.queryByText(/registros perdidos/)).not.toBeInTheDocument()
  })

  it('las dos causas de perdida a la vez salen como dos avisos distintos', async () => {
    api.get.mockResolvedValue({
      data: { ...DATA, registros_perdidos: 2, en_cola: 0, perdidas_por_desborde: 4 },
    })
    renderCosts()
    expect(await screen.findByText('total incompleto: 2 registros perdidos')).toBeInTheDocument()
    expect(
      screen.getByText('total incompleto: se llenó el respaldo y se descartaron 4 registros viejos')
    ).toBeInTheDocument()
  })

  it('con pendientes y marca de vida del drenaje, muestra el ultimo reintento con el locale activo', async () => {
    const cuando = '2026-09-15T18:30:00Z'
    api.get.mockResolvedValue({
      data: { ...DATA, en_cola: 5, ultimo_reintento: cuando },
    })
    renderCosts()
    const esperado = `Último reintento: ${new Date(cuando).toLocaleString('es-HN')}`
    expect(await screen.findByText(esperado)).toBeInTheDocument()
  })

  it('sin marca de vida del drenaje no se inventa una fecha', async () => {
    api.get.mockResolvedValue({ data: { ...DATA, en_cola: 5, ultimo_reintento: null } })
    renderCosts()
    await screen.findByText(/esperando reintento/)
    expect(screen.queryByText(/Último reintento/)).not.toBeInTheDocument()
  })

  it('singular y plural: 1 registro en cola no dice "registros"', async () => {
    api.get.mockResolvedValue({ data: { ...DATA, en_cola: 1 } })
    renderCosts()
    expect(
      await screen.findByText('Hay 1 registro esperando reintento; el total va a completarse solo.')
    ).toBeInTheDocument()
  })

  it('singular y plural: con 1 descarte el verbo concuerda en los dos idiomas', async () => {
    api.get.mockResolvedValue({ data: { ...DATA, perdidas_por_desborde: 1 } })
    const { unmount } = renderCosts()
    expect(
      await screen.findByText('total incompleto: se llenó el respaldo y se descartó 1 registro viejo')
    ).toBeInTheDocument()
    unmount()
    localStorage.setItem('jax_lang', 'en')
    renderCosts()
    expect(
      await screen.findByText('incomplete total: the backup filled up and 1 old record was dropped')
    ).toBeInTheDocument()
  })

  it('los conteos siguen el locale activo (miles agrupados)', async () => {
    api.get.mockResolvedValue({ data: { ...DATA, en_cola: 12345 } })
    renderCosts()
    expect(
      await screen.findByText('Hay 12,345 registros esperando reintento; el total va a completarse solo.')
    ).toBeInTheDocument()
  })

  it('en ingles los tres avisos salen traducidos', async () => {
    localStorage.setItem('jax_lang', 'en')
    api.get.mockResolvedValue({
      data: { ...DATA, registros_perdidos: 2, en_cola: 5, perdidas_por_desborde: 4, ultimo_reintento: '2026-09-15T18:30:00Z' },
    })
    renderCosts()
    expect(
      await screen.findByText('5 records are waiting to be retried; the total will complete on its own.')
    ).toBeInTheDocument()
    expect(screen.getByText('incomplete total: 2 records lost')).toBeInTheDocument()
    expect(
      screen.getByText('incomplete total: the backup filled up and 4 old records were dropped')
    ).toBeInTheDocument()
    expect(
      screen.getByText(`Last retry: ${new Date('2026-09-15T18:30:00Z').toLocaleString('en-US')}`)
    ).toBeInTheDocument()
  })

  // Task 10 (2026-09-16, la fila venenosa): una TERCERA causa de perdida, con
  // su propio texto. "La base la rechazo" manda al admin a mirar el dato de la
  // fila; "se lleno el respaldo" lo manda a mirar el drenaje. Mezclarlas en un
  // solo aviso lo manda donde no es.
  it('las filas que la base rechazo son perdida, con su propio texto', async () => {
    api.get.mockResolvedValue({
      data: { ...DATA, registros_perdidos: 0, en_cola: 0, perdidas_por_desborde: 0, rechazadas: 3 },
    })
    renderCosts()
    expect(
      await screen.findByText('total incompleto: la base rechazó 3 registros, que quedaron en cuarentena')
    ).toBeInTheDocument()
    expect(screen.queryByText(/se llenó el respaldo/)).not.toBeInTheDocument()
  })

  it('singular y plural: 1 rechazada concuerda en los dos idiomas', async () => {
    api.get.mockResolvedValue({ data: { ...DATA, rechazadas: 1 } })
    const { unmount } = renderCosts()
    expect(
      await screen.findByText('total incompleto: la base rechazó 1 registro, que quedó en cuarentena')
    ).toBeInTheDocument()
    unmount()
    localStorage.setItem('jax_lang', 'en')
    renderCosts()
    expect(
      await screen.findByText('incomplete total: the database rejected 1 record, now quarantined')
    ).toBeInTheDocument()
  })

  it('en ingles el aviso de rechazadas sale traducido y en plural', async () => {
    localStorage.setItem('jax_lang', 'en')
    api.get.mockResolvedValue({ data: { ...DATA, rechazadas: 4 } })
    renderCosts()
    expect(
      await screen.findByText('incomplete total: the database rejected 4 records, now quarantined')
    ).toBeInTheDocument()
  })

  it('un backend viejo sin el campo no inventa un aviso', async () => {
    api.get.mockResolvedValue({ data: { ...DATA } })
    renderCosts()
    await screen.findByText('Total')
    expect(screen.queryByText(/cuarentena/)).not.toBeInTheDocument()
  })

  it('los avisos salen aunque no haya filas: "sin datos" solo mentiria', async () => {
    api.get.mockResolvedValue({
      data: { by_facet: [], chart_data: null, en_cola: 5, perdidas_por_desborde: 4 },
    })
    renderCosts()
    expect(
      await screen.findByText('Hay 5 registros esperando reintento; el total va a completarse solo.')
    ).toBeInTheDocument()
    expect(
      screen.getByText('total incompleto: se llenó el respaldo y se descartaron 4 registros viejos')
    ).toBeInTheDocument()
  })
})
