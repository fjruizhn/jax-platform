import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act } from 'react'
import '@testing-library/jest-dom'

// PR-J (2026-09-14): aprobar una propuesta hacia un modelo que no declara el
// contrato de dispatch de la faceta devuelve 409 `modelo_sin_contrato_de_dispatch`.
// El catch de decide() estaba vacío: el rechazo parecía un click que no hizo
// nada. Ahora se dice qué falta, en los dos idiomas.
vi.mock('../../api/client', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))

import api from '../../api/client'
import AdminModelCatalog from './AdminModelCatalog'
import { I18nProvider } from '../../i18n/index.jsx'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

const PROPUESTA = {
  id: 11, facet_key: 'jekyll', current_model_ref: 3, proposed_model_ref: 2111,
  reason: 'drift_detected', detail: 'renombrado', status: 'pending',
}

function rechazo(status, detail) {
  return { response: { status, data: { detail } } }
}

// 2026-09-27: POST /admin/models/sync ya no devuelve el resultado completo
// -- arranca el sync en segundo plano y responde con el id de la ejecución
// (ver api/admin/models.py). La pantalla lo sigue con GET
// /admin/models/sync/estado. `estadoSyncMock` es el estado que ESE GET
// devuelve; los tests que simulan "el sync ya terminó" lo actualizan dentro
// del mock de `api.post`, así que para cuando el componente hace su primer
// refresco (inmediatamente después del 202) el estado YA está resuelto --
// mismo comportamiento real que el `BackgroundTask` de FastAPI bajo
// TestClient (backend/tests/test_admin_models_endpoints.py), corriendo
// antes de que la pantalla pueda preguntar por él.
let estadoSyncMock

// MINOR-6 (auditoría adversarial, 2026-09-27): fechas EXACTAMENTE en la
// forma real que manda el backend (tiempo.iso_utc() -- ISO 8601, zona UTC
// explícita, milisegundos), no un string ambiguo "YYYY-MM-DD HH:MM:SS" sin
// zona (eso es justo lo que un `new Date(...)` de Safari/iPad puede leer
// distinto que Chrome). Si algún día el backend volviera a un formato sin
// zona, estos fixtures dejarían de representar la respuesta real.
function resultadoTerminado(resultado, { id = 1, origen = 'manual' } = {}) {
  return {
    corriendo: null,
    ultima: {
      id, origen, iniciado_por: 1, iniciado_por_email: 'fernando@axioma-ia.io',
      estado: resultado.ok ? 'ok' : 'con_problemas',
      paso_actual: 9, pasos_total: 9, detalle_paso: 'facetas_en_riesgo',
      iniciado_en: '2026-09-27T00:00:00.000+00:00', terminado_en: '2026-09-27T00:00:05.000+00:00',
      resultado,
    },
  }
}

function mockRoutes({ proposals = [PROPUESTA], models = [], maxTokensOpts } = {}) {
  api.get.mockImplementation(url => {
    if (url.startsWith('/admin/models/sync/estado')) return Promise.resolve({ data: estadoSyncMock })
    if (url.startsWith('/admin/models/proposals')) return Promise.resolve({ data: { proposals } })
    return Promise.resolve({
      data: { models, ...(maxTokensOpts ? { max_tokens_param_opciones: maxTokensOpts } : {}) },
    })
  })
}

// Configura `api.post` para que, al llamarse, deje el GET de estado
// apuntando a un sync YA TERMINADO con `resultado` -- así el primer refresco
// que hace la pantalla después del 202 ya ve el desenlace final.
function mockSyncTerminado(resultado, opts) {
  api.post.mockImplementation(async () => {
    estadoSyncMock = resultadoTerminado(resultado, opts)
    return { data: { ejecucion_id: opts?.id ?? 1, pasos_total: 9 } }
  })
}

function renderCatalogo() {
  mockRoutes()
  return render(<I18nProvider><AdminModelCatalog /></I18nProvider>)
}

beforeEach(() => {
  api.get.mockReset(); api.post.mockReset(); api.put.mockReset()
  localStorage.clear()
  estadoSyncMock = { corriendo: null, ultima: null }
})

// Task 3 (2026-09-15): /admin/models/sync decía ok:true aunque fallaran todos
// los providers, y la pantalla no leía el cuerpo. Ahora el backend manda `ok`
// calculado, `code: 'sync_con_errores'` y la lista de lo que falló.
describe('AdminModelCatalog -- un sync con errores no se ve como éxito', () => {
  it('el texto existe en los dos idiomas y nombra lo que falló', () => {
    expect(es.sync_con_errores('openai, models.dev')).toContain('openai, models.dev')
    expect(en.sync_con_errores('openai, models.dev')).toContain('openai, models.dev')
  })

  it('ok:false nombra los providers que fallaron y el enriquecimiento', async () => {
    renderCatalogo()
    mockSyncTerminado({
      ok: false, code: 'sync_con_errores', providers_fallidos: ['openai', 'gemini'],
      enrich_fallido: true, providers: [], enrich: { error: 'x' },
    })
    fireEvent.click(screen.getByText(es.adminModelsSync))
    expect(await screen.findByText(es.sync_con_errores('openai, gemini, models.dev'))).toBeInTheDocument()
  })

  it('ok:true no muestra ningún aviso de error', async () => {
    renderCatalogo()
    mockSyncTerminado({ ok: true, providers_fallidos: [], enrich_fallido: false, providers: [], enrich: {} })
    fireEvent.click(screen.getByText(es.adminModelsSync))
    await waitFor(() => expect(screen.getByText(es.adminModelsSync)).not.toBeDisabled())
    expect(screen.queryByText(/openai|models\.dev/)).not.toBeInTheDocument()
    expect(screen.queryByText(es.adminModelsSyncError)).not.toBeInTheDocument()
  })
})

// 2026-09-27: un proveedor saltado (anthropic sin credencial de jaxsvc,
// ollama caído...) o una faceta 'primary' cuyo modelo dejó de estar
// disponible ahora también bajan `ok` (ver model_catalog.sync_all()) -- la
// pantalla tiene que nombrarlos, no solo los `providers_fallidos`.
describe('AdminModelCatalog -- un sync con proveedores saltados o facetas en riesgo se ve', () => {
  it('los textos existen en los dos idiomas y nombran lo que pasó', () => {
    expect(es.sync_con_saltados('anthropic')).toContain('anthropic')
    expect(en.sync_con_saltados('anthropic')).toContain('anthropic')
    expect(es.sync_facetas_en_riesgo('jekyll (deprecated)')).toContain('jekyll (deprecated)')
    expect(en.sync_facetas_en_riesgo('jekyll (deprecated)')).toContain('jekyll (deprecated)')
  })

  it('ok:false por un proveedor saltado lo nombra', async () => {
    renderCatalogo()
    mockSyncTerminado({
      ok: false, code: 'sync_con_errores', providers_fallidos: [], enrich_fallido: false,
      providers_saltados: ['anthropic'], facetas_en_riesgo: [], nuevos: {}, providers: [], enrich: {},
    })
    fireEvent.click(screen.getByText(es.adminModelsSync))
    expect(await screen.findByText(es.sync_con_saltados('anthropic'))).toBeInTheDocument()
  })

  it('MINOR-6: ok:false solo por saltados no muestra "fallaron" con lista vacía', async () => {
    renderCatalogo()
    mockSyncTerminado({
      ok: false, code: 'sync_con_errores', providers_fallidos: [], enrich_fallido: false,
      providers_saltados: ['anthropic'], facetas_en_riesgo: [], nuevos: {}, providers: [], enrich: {},
    })
    fireEvent.click(screen.getByText(es.adminModelsSync))
    await screen.findByText(es.sync_con_saltados('anthropic'))
    expect(screen.queryByText(es.sync_con_errores(''))).not.toBeInTheDocument()
  })

  it('MINOR-6: ok:false solo por facetas en riesgo no muestra "fallaron" con lista vacía', async () => {
    renderCatalogo()
    mockSyncTerminado({
      ok: false, code: 'sync_con_errores', providers_fallidos: [], enrich_fallido: false,
      providers_saltados: [],
      facetas_en_riesgo: [{ facet_key: 'jekyll', provider_id: 'deepseek', model_id: 'deepseek-v4-flash', status: 'deprecated' }],
      nuevos: {}, providers: [], enrich: {},
    })
    fireEvent.click(screen.getByText(es.adminModelsSync))
    await screen.findByText(es.sync_facetas_en_riesgo('jekyll (deprecated)'))
    expect(screen.queryByText(es.sync_con_errores(''))).not.toBeInTheDocument()
  })

  it('ok:false por una faceta en riesgo la nombra con su estado', async () => {
    renderCatalogo()
    mockSyncTerminado({
      ok: false, code: 'sync_con_errores', providers_fallidos: [], enrich_fallido: false,
      providers_saltados: [],
      facetas_en_riesgo: [{ facet_key: 'jekyll', provider_id: 'deepseek', model_id: 'deepseek-v4-flash', status: 'deprecated' }],
      nuevos: {}, providers: [], enrich: {},
    })
    fireEvent.click(screen.getByText(es.adminModelsSync))
    expect(await screen.findByText(es.sync_facetas_en_riesgo('jekyll (deprecated)'))).toBeInTheDocument()
  })

  it('ok:true sin saltados ni riesgos no muestra ningún aviso de ese tipo', async () => {
    renderCatalogo()
    mockSyncTerminado({
      ok: true, providers_fallidos: [], enrich_fallido: false,
      providers_saltados: [], facetas_en_riesgo: [], nuevos: {}, providers: [], enrich: {},
    })
    fireEvent.click(screen.getByText(es.adminModelsSync))
    await waitFor(() => expect(screen.getByText(es.adminModelsSync)).not.toBeDisabled())
    expect(screen.queryByText(es.sync_con_saltados('anthropic'))).not.toBeInTheDocument()
    expect(screen.queryByText(es.sync_facetas_en_riesgo('jekyll (deprecated)'))).not.toBeInTheDocument()
  })
})

// MINOR-6 (cuarta auditoría adversarial, 2026-09-28): un candado ocupado por
// otro sync (`code: 'sync_en_curso'`) no es un error del catálogo -- no se
// tocó nada. 2026-09-27: ahora es un 409 del POST (ver
// api/admin/models.py::sync_models), no un 200 con `ok:false` -- el
// mensaje sigue siendo el mismo, "no se tocó nada".
describe('AdminModelCatalog -- un sync con el candado ocupado se ve distinto de un error', () => {
  it('el texto existe en los dos idiomas', () => {
    expect(es.sync_en_curso).toBeTruthy()
    expect(en.sync_en_curso).toBeTruthy()
  })

  it('409 sync_en_curso muestra su propio mensaje, no "fallaron"', async () => {
    renderCatalogo()
    const error409 = {
      response: { status: 409, data: {
        ok: false, code: 'sync_en_curso', providers_fallidos: [], enrich_fallido: false,
        providers_saltados: [], facetas_en_riesgo: [], nuevos: {}, providers: [], enrich: {},
        ejecucion_id: null,
      } },
    }
    api.post.mockRejectedValue(error409)
    fireEvent.click(screen.getByText(es.adminModelsSync))
    expect(await screen.findByText(es.sync_en_curso)).toBeInTheDocument()
    expect(screen.queryByText(es.sync_con_errores(''))).not.toBeInTheDocument()
  })

  it('un sync normal posterior limpia el aviso de candado ocupado', async () => {
    renderCatalogo()
    api.post.mockRejectedValueOnce({
      response: { status: 409, data: {
        ok: false, code: 'sync_en_curso', providers_fallidos: [], enrich_fallido: false,
        providers_saltados: [], facetas_en_riesgo: [], nuevos: {}, providers: [], enrich: {},
        ejecucion_id: null,
      } },
    })
    fireEvent.click(screen.getByText(es.adminModelsSync))
    await screen.findByText(es.sync_en_curso)

    mockSyncTerminado({
      ok: true, providers_fallidos: [], enrich_fallido: false,
      providers_saltados: [], facetas_en_riesgo: [], nuevos: {}, providers: [], enrich: {},
    })
    fireEvent.click(screen.getByText(es.adminModelsSync))
    await waitFor(() => expect(screen.getByText(es.adminModelsSync)).not.toBeDisabled())
    expect(screen.queryByText(es.sync_en_curso)).not.toBeInTheDocument()
  })
})

// 2026-09-27: modelos nuevos detectados por el sync -- informativo, no es un
// error (ok puede seguir true), pero un superadmin quiere saber que apareció
// algo sin comparar el catálogo entero a mano.
describe('AdminModelCatalog -- modelos nuevos detectados por el sync', () => {
  it('el texto existe en los dos idiomas y nombra lo que apareció', () => {
    expect(es.sync_modelos_nuevos('claude-opus-5-nuevo')).toContain('claude-opus-5-nuevo')
    expect(en.sync_modelos_nuevos('claude-opus-5-nuevo')).toContain('claude-opus-5-nuevo')
  })

  it('ok:true con nuevos los muestra', async () => {
    renderCatalogo()
    mockSyncTerminado({
      ok: true, providers_fallidos: [], enrich_fallido: false, providers_saltados: [],
      facetas_en_riesgo: [], nuevos: { anthropic: ['claude-opus-5-nuevo'] }, providers: [], enrich: {},
    })
    fireEvent.click(screen.getByText(es.adminModelsSync))
    expect(await screen.findByText(es.sync_modelos_nuevos('claude-opus-5-nuevo'))).toBeInTheDocument()
  })

  it('sin nuevos no muestra el aviso', async () => {
    renderCatalogo()
    mockSyncTerminado({
      ok: true, providers_fallidos: [], enrich_fallido: false, providers_saltados: [],
      facetas_en_riesgo: [], nuevos: {}, providers: [], enrich: {},
    })
    fireEvent.click(screen.getByText(es.adminModelsSync))
    await waitFor(() => expect(screen.getByText(es.adminModelsSync)).not.toBeDisabled())
    expect(screen.queryByText(/claude-opus-5-nuevo/)).not.toBeInTheDocument()
  })
})

describe('AdminModelCatalog -- una aprobación rechazada se ve', () => {
  it('los textos existen en los dos idiomas', () => {
    for (const clave of ['modelo_sin_contrato_de_dispatch', 'adminProposalsDecideError']) {
      expect(es[clave], `es.${clave}`).toBeTruthy()
      expect(en[clave], `en.${clave}`).toBeTruthy()
    }
    expect(es.modelo_sin_contrato_de_dispatch('m', 'x')).toContain('x')
    expect(en.modelo_sin_contrato_de_dispatch('m', 'x')).toContain('x')
  })

  it('el 409 de contrato nombra el modelo y las columnas que faltan', async () => {
    renderCatalogo()
    api.post.mockRejectedValue(rechazo(409, {
      code: 'modelo_sin_contrato_de_dispatch', model_id: 'deepseek-flash',
      campos: ['max_tokens_param', 'max_output_tokens'], message: '...',
    }))
    fireEvent.click(await screen.findByRole('button', { name: es.adminProposalsApprove }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      es.modelo_sin_contrato_de_dispatch('deepseek-flash', 'max_tokens_param, max_output_tokens'),
    )
  })

  it('el 409 de otro proveedor nombra los dos proveedores', async () => {
    renderCatalogo()
    expect(en.modelo_de_otro_proveedor('m', 'a', 'b')).toContain('b')
    api.post.mockRejectedValue(rechazo(409, {
      code: 'modelo_de_otro_proveedor', model_id: 'gpt-x', campos: ['provider_id'],
      provider_modelo: 'openai', provider_binding: 'deepseek', message: '...',
    }))
    fireEvent.click(await screen.findByRole('button', { name: es.adminProposalsApprove }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      es.modelo_de_otro_proveedor('gpt-x', 'openai', 'deepseek'),
    )
  })

  it('otro error cae en el texto genérico, no en silencio', async () => {
    renderCatalogo()
    api.post.mockRejectedValue(rechazo(500, 'lo_que_sea'))
    fireEvent.click(await screen.findByRole('button', { name: es.adminProposalsApprove }))
    expect(await screen.findByRole('alert')).toHaveTextContent(es.adminProposalsDecideError)
  })
})

// PR-L (2026-09-14, Ruling 33): el 409 ya no es un callejón sin salida. La
// propuesta muestra su último rechazo (rastro en model_catalog_audit) y tanto
// ese rastro como el 409 en vivo ofrecen "declarar contrato" sobre la fila.
const MODELO_SIN_CONTRATO = {
  id: 2111, provider_id: 'deepseek', model_id: 'deepseek-flash', status: 'available', source: 'provider_api',
  max_tokens_param: null, max_output_tokens: null,
}
const MODELO_CON_CONTRATO = {
  id: 3, provider_id: 'deepseek', model_id: 'deepseek-v4-flash', status: 'available', source: 'provider_api',
  max_tokens_param: 'max_tokens', max_output_tokens: 393216,
}
const RECHAZO_GUARDADO = {
  code: 'modelo_sin_contrato_de_dispatch', model_ref: 2111, model_id: 'deepseek-flash',
  campos: ['max_tokens_param', 'max_output_tokens'], performed_by: 1, performed_at: '2026-09-14 10:00:00',
}

function renderConModelos(propuesta = PROPUESTA) {
  mockRoutes({
    proposals: [propuesta],
    models: [MODELO_SIN_CONTRATO, MODELO_CON_CONTRATO],
    maxTokensOpts: ['max_tokens', 'max_completion_tokens'],
  })
  return render(<I18nProvider><AdminModelCatalog /></I18nProvider>)
}

// Task 16b (Ruling 28): "deprecado" se ve distinto de "degradado" -- antes
// los dos pintaban text-aviso y sólo los distinguía la palabra.
const MODELO_DEPRECADO = {
  id: 4, provider_id: 'deepseek', model_id: 'deepseek-v3', status: 'deprecated', source: 'provider_api',
  max_tokens_param: 'max_tokens', max_output_tokens: 131072,
}
const MODELO_DEGRADADO = {
  id: 5, provider_id: 'deepseek', model_id: 'deepseek-v3-mini', status: 'degraded', source: 'provider_api',
  max_tokens_param: 'max_tokens', max_output_tokens: 131072,
}

describe('AdminModelCatalog -- "deprecado" se distingue de "degradado" (Ruling 28)', () => {
  it('deprecated pinta text-obsoleto y degraded se queda en text-aviso', async () => {
    mockRoutes({ proposals: [], models: [MODELO_DEPRECADO, MODELO_DEGRADADO] })
    render(<I18nProvider><AdminModelCatalog /></I18nProvider>)
    expect(await screen.findByText(es.adminModelsStatusDeprecated)).toHaveClass('text-obsoleto')
    expect(screen.getByText(es.adminModelsStatusDegraded)).toHaveClass('text-aviso')
  })
})

describe('AdminModelCatalog -- declarar contrato de dispatch (PR-L)', () => {
  it('la propuesta muestra su último rechazo guardado y ofrece declarar el contrato', async () => {
    renderConModelos({ ...PROPUESTA, ultimo_rechazo: RECHAZO_GUARDADO })
    expect(await screen.findByText(es.adminProposalsUltimoRechazo('2026-09-14 10:00'), { exact: false })).toBeInTheDocument()
    expect(screen.getByText(
      es.modelo_sin_contrato_de_dispatch('deepseek-flash', 'max_tokens_param, max_output_tokens'), { exact: false },
    )).toBeInTheDocument()
    // La tabla de propuestas es la primera; la del catálogo tiene su propio
    // "declarar contrato" por fila.
    const [tablaPropuestas] = screen.getAllByRole('table')
    fireEvent.click(within(tablaPropuestas).getByRole('button', { name: es.adminContratoDeclarar }))
    expect(await screen.findByText(es.adminContratoTitulo('deepseek/deepseek-flash'))).toBeInTheDocument()
  })

  it('el 409 en vivo de approve ofrece declarar el contrato de esa fila', async () => {
    renderConModelos()
    api.post.mockRejectedValue(rechazo(409, {
      code: 'modelo_sin_contrato_de_dispatch', model_ref: 2111, model_id: 'deepseek-flash',
      campos: ['max_output_tokens'], message: '...',
    }))
    fireEvent.click(await screen.findByRole('button', { name: es.adminProposalsApprove }))
    const alerta = await screen.findByRole('alert')
    fireEvent.click(within(alerta).getByRole('button', { name: es.adminContratoDeclarar }))
    expect(await screen.findByText(es.adminContratoTitulo('deepseek/deepseek-flash'))).toBeInTheDocument()
  })

  it('el 409 de otro proveedor NO ofrece declarar contrato (no es el remedio)', async () => {
    renderConModelos()
    api.post.mockRejectedValue(rechazo(409, {
      code: 'modelo_de_otro_proveedor', model_ref: 2111, model_id: 'deepseek-flash', campos: ['provider_id'],
      provider_modelo: 'openai', provider_binding: 'deepseek', message: '...',
    }))
    fireEvent.click(await screen.findByRole('button', { name: es.adminProposalsApprove }))
    const alerta = await screen.findByRole('alert')
    expect(within(alerta).queryByRole('button', { name: es.adminContratoDeclarar })).not.toBeInTheDocument()
  })

  it('el catálogo muestra el contrato de cada fila y declara el de una desde la tabla', async () => {
    api.put.mockResolvedValue({ data: { ok: true } })
    renderConModelos({ ...PROPUESTA, proposed_model_ref: 3 })
    expect(await screen.findByText(es.adminModelsContrato)).toBeInTheDocument()
    expect(screen.getByText(es.adminModelsContratoSinDeclarar)).toBeInTheDocument()
    expect(screen.getByText('max_tokens · 393216')).toBeInTheDocument()
    const tablaCatalogo = screen.getAllByRole('table')[1]
    const botones = within(tablaCatalogo).getAllByRole('button', { name: es.adminContratoDeclarar })
    expect(botones).toHaveLength(2)
    fireEvent.click(botones[0])  // fila 2111, la primera del catálogo
    fireEvent.change(await screen.findByLabelText(es.adminContratoParam), { target: { value: 'max_tokens' } })
    fireEvent.change(screen.getByLabelText(es.adminContratoTope), { target: { value: '393216' } })
    fireEvent.click(screen.getByRole('button', { name: es.adminContratoGuardar }))
    expect(await screen.findByText(es.adminContratoGuardado)).toBeInTheDocument()
    expect(api.put).toHaveBeenCalledWith('/admin/models/2111/contrato-dispatch', {
      max_tokens_param: 'max_tokens', max_output_tokens: 393216,
    })
  })
})

// 2026-09-27: avance real (barra/paso) y "última actualización" siempre
// visible (pedido de Fernando).
describe('AdminModelCatalog -- avance real y última actualización', () => {
  it('sin ninguna corrida todavía, dice "nunca"', async () => {
    renderCatalogo()
    expect(await screen.findByText(es.adminModelsUltimaActualizacionNunca)).toBeInTheDocument()
  })

  it('con una última corrida manual, muestra fecha, origen y quién', async () => {
    estadoSyncMock = resultadoTerminado({ ok: true, providers: [], enrich: {}, providers_fallidos: [], providers_saltados: [], enrich_fallido: false, nuevos: {}, facetas_en_riesgo: [] })
    renderCatalogo()
    expect(await screen.findByText(es.adminModelsUltimaActualizacionTitulo, { exact: false })).toBeInTheDocument()
    expect(screen.getByText(es.adminModelsUltimaActualizacionManual('fernando@axioma-ia.io'), { exact: false })).toBeInTheDocument()
  })

  it('si hay un sync corriendo al abrir la pantalla, se muestra su avance', async () => {
    estadoSyncMock = {
      corriendo: {
        id: 7, origen: 'programado', iniciado_por: null, estado: 'corriendo',
        paso_actual: 3, pasos_total: 9, detalle_paso: 'gemini',
        iniciado_en: new Date().toISOString(), terminado_en: null, resultado: null,
      },
      ultima: null,
    }
    renderCatalogo()
    expect(await screen.findByText(es.adminModelsSyncProgreso(3, 9, 'gemini'))).toBeInTheDocument()
  })

  it('el botón Sincronizar muestra el paso en curso mientras hay un sync corriendo', async () => {
    renderCatalogo()
    let resuelto
    api.post.mockImplementation(() => new Promise(resolve => { resuelto = resolve }))
    fireEvent.click(screen.getByText(es.adminModelsSync))
    expect(await screen.findByText(es.adminModelsSyncing)).toBeInTheDocument()
    resuelto({ data: { ejecucion_id: 1, pasos_total: 9 } })
  })
})

// 2026-09-27: modal de programación (encender/apagar, cada cuánto corre).
describe('AdminModelCatalog -- botón de Programación', () => {
  it('abre el modal con la configuración traída del backend', async () => {
    renderCatalogo()
    api.get.mockImplementation(url => {
      if (url.startsWith('/admin/models/sync/config')) {
        return Promise.resolve({ data: {
          habilitado: true, cada_valor: 6, cada_unidad: 'horas',
          actualizado_por: null, actualizado_en: null, proxima_corrida_estimada: null,
        } })
      }
      if (url.startsWith('/admin/models/sync/estado')) return Promise.resolve({ data: estadoSyncMock })
      if (url.startsWith('/admin/models/proposals')) return Promise.resolve({ data: { proposals: [] } })
      return Promise.resolve({ data: { models: [] } })
    })

    fireEvent.click(await screen.findByRole('button', { name: es.adminModelsProgramacionAbrir }))

    expect(await screen.findByText(es.adminModelsProgramacionTitulo)).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// MINOR-7/MINOR-8 (auditoría adversarial, 2026-09-27): el polling de
// GET /admin/models/sync/estado se DETIENE -- cuando el sync termina, al
// desmontar, y un error de red no reintenta para siempre en silencio.
// --------------------------------------------------------------------------

function corriendoFixture(overrides = {}) {
  return {
    id: 9, origen: 'manual', iniciado_por: 1, estado: 'corriendo',
    paso_actual: 1, pasos_total: 9, detalle_paso: 'openai',
    iniciado_en: new Date().toISOString(), terminado_en: null, resultado: null,
    ...overrides,
  }
}

function llamadasAEstado() {
  return api.get.mock.calls.filter(([url]) => url.startsWith('/admin/models/sync/estado')).length
}

describe('AdminModelCatalog -- el polling se detiene', () => {
  // Fake timers desde ANTES del render, durante TODO el test: un intervalo
  // creado por React mientras corren timers REALES sigue siendo un
  // intervalo REAL para siempre (cambiar a fake timers a mitad de camino
  // no lo "adopta") -- y con fake timers activos, `findByText`/`waitFor` no
  // sirven (su propio polling interno usa `setTimeout`, que con el reloj
  // congelado nunca dispara solo). Por eso acá todo se resuelve con
  // `act(async () => { await vi.advanceTimersByTimeAsync(0) })` (deja
  // correr los microtasks pendientes sin mover el reloj) + aserciones
  // SÍNCRONAS (`getByText`/`queryByText`), nunca `findBy*`/`waitFor`.
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  async function flush() {
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  }

  it('deja de pedir /sync/estado una vez que el sync termina', async () => {
    estadoSyncMock = { corriendo: corriendoFixture(), ultima: null }
    mockRoutes()
    render(<I18nProvider><AdminModelCatalog /></I18nProvider>)
    await flush()

    expect(screen.getByText(es.adminModelsSyncProgreso(1, 9, 'openai'))).toBeInTheDocument()
    const llamadasCorriendo = llamadasAEstado()

    estadoSyncMock = resultadoTerminado({
      ok: true, providers: [], enrich: {}, providers_fallidos: [], providers_saltados: [],
      enrich_fallido: false, nuevos: {}, facetas_en_riesgo: [],
    }, { id: 9 })

    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(screen.getByText(es.adminModelsSync)).not.toBeDisabled()
    const llamadasTerminado = llamadasAEstado()
    expect(llamadasTerminado).toBeGreaterThan(llamadasCorriendo)

    // Varios intervalos más: si el polling no se hubiera detenido, habría
    // seguido pidiendo /sync/estado.
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(llamadasAEstado()).toBe(llamadasTerminado)
  })

  it('deja de pedir /sync/estado al desmontar', async () => {
    estadoSyncMock = { corriendo: corriendoFixture(), ultima: null }
    mockRoutes()
    const { unmount } = render(<I18nProvider><AdminModelCatalog /></I18nProvider>)
    await flush()

    expect(screen.getByText(es.adminModelsSyncProgreso(1, 9, 'openai'))).toBeInTheDocument()
    const llamadasAntes = llamadasAEstado()

    unmount()

    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(llamadasAEstado()).toBe(llamadasAntes)
  })

  it('un error de red durante el polling muestra un mensaje y un botón de reintentar', async () => {
    estadoSyncMock = { corriendo: corriendoFixture(), ultima: null }
    mockRoutes()
    render(<I18nProvider><AdminModelCatalog /></I18nProvider>)
    await flush()

    expect(screen.getByText(es.adminModelsSyncProgreso(1, 9, 'openai'))).toBeInTheDocument()

    api.get.mockImplementation(url => {
      if (url.startsWith('/admin/models/sync/estado')) return Promise.reject(new Error('network down'))
      return Promise.resolve({ data: { models: [] } })
    })

    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(screen.getByText(es.adminModelsSyncPollingError)).toBeInTheDocument()
    // El avance se limpia -- no queda mostrando un paso viejo mientras dice que hay un error.
    expect(screen.queryByText(es.adminModelsSyncProgreso(1, 9, 'openai'))).not.toBeInTheDocument()

    // Reintentar, con la red ya recuperada.
    estadoSyncMock = resultadoTerminado({
      ok: true, providers: [], enrich: {}, providers_fallidos: [], providers_saltados: [],
      enrich_fallido: false, nuevos: {}, facetas_en_riesgo: [],
    }, { id: 9 })
    mockRoutes()

    fireEvent.click(screen.getByRole('button', { name: es.adminModelsSyncReintentar }))
    await flush()

    expect(screen.queryByText(es.adminModelsSyncPollingError)).not.toBeInTheDocument()
  })
})
