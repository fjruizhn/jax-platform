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

const LIMITES = {
  session_timeout_min: { min: 15, max: 10080 },
  max_pipelines: { min: 1, max: 3 },
  lang_default: { opciones: ['es', 'en'] },
  system_name: { max_largo: 60 },
}
const CONFIG = { data: { config: [{ key: 'system_name', value: 'Axioma' }], limites: LIMITES } }

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
    for (const clave of ['config_clave_reservada', 'config_clave_invalida', 'config_collation_desconocida', 'adminSettingsSaveError', 'adminSettingsLoadError']) {
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

// Frente C (2026-09-16): los cinco ajustes mandan. La pantalla toma los rangos
// del servidor (antes: sesión 5..1440 cuando rige 10080, pipelines 1..5 cuando
// Jacobs no corre más de 3) y no muestra valores inventados (antes '60', '1',
// '7' cuando faltaba la fila).
describe('AdminSettings -- ajustes que mandan', () => {
  const COMPLETA = { data: { config: [
    { key: 'system_name', value: 'Axioma' }, { key: 'lang_default', value: 'es' },
    { key: 'theme_default', value: 'dark' }, { key: 'session_timeout_min', value: '10080' },
    { key: 'max_pipelines', value: '3' },
  ], limites: LIMITES } }

  it('los campos toman mínimo, máximo y largo del servidor', async () => {
    api.get.mockResolvedValue(COMPLETA)
    renderSettings()
    const sesion = await screen.findByLabelText(es.adminSettingsTimeout)
    expect(sesion).toHaveAttribute('min', '15')
    expect(sesion).toHaveAttribute('max', '10080')
    expect(sesion).toHaveValue(10080)
    expect(screen.getByLabelText(es.adminSettingsMaxPipelines)).toHaveAttribute('max', '3')
    expect(screen.getByLabelText(es.adminSettingsSystemName)).toHaveAttribute('maxlength', '60')
  })

  it('sin fila guardada un campo queda vacío, no con un valor inventado', async () => {
    api.get.mockResolvedValue(CONFIG)
    renderSettings()
    expect(await screen.findByLabelText(es.adminSettingsTimeout)).toHaveValue(null)
    expect(screen.getByLabelText(es.adminSettingsMaxPipelines)).toHaveValue(null)
  })

  // T16 (2026-10-02): web_task_retention_days se retiró con /command (el reaper
  // que lo leía se borró). Ningún campo ni etiqueta de retención en la pantalla.
  it('no hay campo de retención de tareas web y su limite no se pinta', async () => {
    api.get.mockResolvedValue(COMPLETA)
    renderSettings()
    await screen.findByLabelText(es.adminSettingsTimeout)
    expect(document.getElementById('ajuste-retencion')).toBeNull()
    expect(screen.queryByText(/Retención de tareas web/)).not.toBeInTheDocument()
    expect(es.adminSettingsRetention).toBeUndefined()
    expect(en.adminSettingsRetention).toBeUndefined()
    expect(es.adminSettingsRetentionAyuda).toBeUndefined()
    expect(en.adminSettingsRetentionAyuda).toBeUndefined()
  })

  it('un valor fuera de rango se nombra con la etiqueta del campo, en los dos idiomas', async () => {
    api.get.mockResolvedValue(CONFIG)
    api.put.mockRejectedValue(rechazo(400, { code: 'config_valor_invalido', clave: 'max_pipelines' }))
    renderSettings()
    await guardar()
    expect(await screen.findByRole('alert')).toHaveTextContent(es.config_valor_invalido(es.adminSettingsMaxPipelines))
    expect(en.config_valor_invalido(en.adminSettingsMaxPipelines)).toContain(en.adminSettingsMaxPipelines)
  })

  // Revisión final (2026-09-17): sin `clave` el texto decía "undefined".
  for (const [nombre, detail] of [['como texto', 'config_valor_invalido'], ['sin clave', { code: 'config_valor_invalido' }]]) {
    it(`config_valor_invalido ${nombre} cae en el texto genérico, no en "undefined"`, async () => {
      api.get.mockResolvedValue(CONFIG)
      api.put.mockRejectedValue(rechazo(400, detail))
      renderSettings()
      await guardar()
      const alerta = await screen.findByRole('alert')
      expect(alerta).toHaveTextContent(es.adminSettingsSaveError)
      expect(alerta).not.toHaveTextContent('undefined')
    })
  }

  it('las ayudas existen en los dos idiomas', () => {
    for (const t of [es, en]) {
      expect(t.adminSettingsTimeoutAyuda).toBeTruthy()
      expect(t.adminSettingsMaxPipelinesAyuda(3)).toContain('3')
    }
  })

  it('un guardado bueno vuelve a pedir la apariencia', async () => {
    api.get.mockResolvedValue(CONFIG)
    api.put.mockResolvedValue({ data: { ok: true } })
    renderSettings()
    await guardar()
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/apariencia'))
  })
})

describe('AdminSettings -- umbral de confirmación de costo (spec 2026-09-17 §6.1)', () => {
  const CON_UMBRAL = { data: {
    config: [{ key: 'system_name', value: 'Axioma' }, { key: 'pipeline_confirmar_usd', value: '0.50' }],
    limites: { ...LIMITES, pipeline_confirmar_usd: { min: '0', max: '999999.99', decimales: 2 } },
  } }

  it('el campo toma mínimo, máximo y paso del servidor', async () => {
    api.get.mockResolvedValue(CON_UMBRAL)
    renderSettings()
    const campo = await screen.findByLabelText(es.adminSettingsConfirmarUsd)
    expect(campo).toHaveValue(0.5)
    expect(campo).toHaveAttribute('min', '0')
    expect(campo).toHaveAttribute('max', '999999.99')
    expect(campo).toHaveAttribute('step', '0.01')
  })

  it('un umbral fuera de rango se nombra con la etiqueta del campo', async () => {
    api.get.mockResolvedValue(CON_UMBRAL)
    api.put.mockRejectedValue(rechazo(400, { code: 'config_valor_invalido', clave: 'pipeline_confirmar_usd' }))
    renderSettings()
    await guardar()
    expect(await screen.findByRole('alert')).toHaveTextContent(es.config_valor_invalido(es.adminSettingsConfirmarUsd))
  })

  it('la etiqueta y la ayuda existen en los dos idiomas y difieren', () => {
    for (const clave of ['adminSettingsConfirmarUsd', 'adminSettingsConfirmarUsdAyuda']) {
      expect(es[clave], `es.${clave}`).toBeTruthy()
      expect(en[clave], `en.${clave}`).toBeTruthy()
      expect(es[clave]).not.toBe(en[clave])
    }
  })
})

describe('AdminSettings -- tope de devoluciones del árbitro (2026-09-20)', () => {
  // El numero existia en axioma_config desde jax-platform#121 pero NO en la
  // pantalla: la unica forma de cambiarlo era un UPDATE a mano, que
  // config_audit prohibe. Un numero de gobernanza que nadie podia tocar.
  const CON_TOPE = { data: {
    config: [{ key: 'system_name', value: 'Axioma' }, { key: 'jacobs.tope_devoluciones', value: '2' }],
    limites: { ...LIMITES, 'jacobs.tope_devoluciones': { min: 0, max: 5 } },
  } }

  it('el campo toma mínimo y máximo del servidor, no del código', async () => {
    api.get.mockResolvedValue(CON_TOPE)
    renderSettings()
    const campo = await screen.findByLabelText(es.adminSettingsTopeDevoluciones)
    expect(campo).toHaveValue(2)
    expect(campo).toHaveAttribute('min', '0')
    expect(campo).toHaveAttribute('max', '5')
  })

  it('un valor fuera de rango se nombra con la etiqueta del campo, no con la clave técnica', async () => {
    api.get.mockResolvedValue(CON_TOPE)
    api.put.mockRejectedValue(rechazo(400, { code: 'config_valor_invalido', clave: 'jacobs.tope_devoluciones' }))
    renderSettings()
    await guardar()
    expect(await screen.findByRole('alert')).toHaveTextContent(es.config_valor_invalido(es.adminSettingsTopeDevoluciones))
  })

  it('la etiqueta y la ayuda existen en los dos idiomas y difieren', () => {
    for (const clave of ['adminSettingsTopeDevoluciones', 'adminSettingsTopeDevolucionesAyuda']) {
      expect(es[clave], `es.${clave}`).toBeTruthy()
      expect(en[clave], `en.${clave}`).toBeTruthy()
      expect(es[clave]).not.toBe(en[clave])
    }
  })

  it('la ayuda explica qué significa CERO, que es el caso que confunde', () => {
    // Cero no es "apagado": el arbitro puede objetar pero no devuelve, y la
    // primera objecion termina el pipeline en `disputed`. Si la ayuda no lo
    // dice, alguien va a poner 0 creyendo que desactiva el arbitro.
    expect(es.adminSettingsTopeDevolucionesAyuda.toLowerCase()).toContain('0')
    expect(en.adminSettingsTopeDevolucionesAyuda.toLowerCase()).toContain('0')
  })
})

describe('AdminSettings -- edad máxima del respaldo de C2 (2026-10-06)', () => {
  // La clave existía en axioma_config (sembrada con 86400) pero no en la pantalla:
  // el superadmin no podía llevarla a 108000 (30 h, el diseño de C2).
  const CLAVE = 'ejecutor.c2_edad_max_s'
  const CON_C2 = { data: {
    config: [{ key: 'system_name', value: 'Axioma' }, { key: CLAVE, value: '86400' }],
    limites: { ...LIMITES, [CLAVE]: { min: 3600, max: 604800 } },
  } }

  it('el campo aparece con el valor y toma mínimo y máximo del servidor', async () => {
    api.get.mockResolvedValue(CON_C2)
    renderSettings()
    const campo = await screen.findByLabelText(es.adminSettingsC2EdadMax)
    expect(campo).toHaveValue(86400)
    expect(campo).toHaveAttribute('min', '3600')
    expect(campo).toHaveAttribute('max', '604800')
  })

  it('el valor editado se envía con su clave', async () => {
    api.get.mockResolvedValue(CON_C2)
    api.put.mockResolvedValue({ data: { ok: true } })
    renderSettings()
    const campo = await screen.findByLabelText(es.adminSettingsC2EdadMax)
    fireEvent.change(campo, { target: { value: '108000' } })
    await guardar()
    await waitFor(() => expect(api.put).toHaveBeenCalled())
    expect(api.put.mock.calls[0][1]).toContainEqual({ key: CLAVE, value: '108000' })
  })

  it('un valor fuera de rango se nombra con la etiqueta del campo, no con la clave técnica', async () => {
    api.get.mockResolvedValue(CON_C2)
    api.put.mockRejectedValue(rechazo(400, { code: 'config_valor_invalido', clave: CLAVE }))
    renderSettings()
    await guardar()
    expect(await screen.findByRole('alert')).toHaveTextContent(es.config_valor_invalido(es.adminSettingsC2EdadMax))
  })

  it('la etiqueta y la ayuda existen en los dos idiomas, difieren, y la ayuda dice 108000', () => {
    for (const clave of ['adminSettingsC2EdadMax', 'adminSettingsC2EdadMaxAyuda']) {
      expect(es[clave], `es.${clave}`).toBeTruthy()
      expect(en[clave], `en.${clave}`).toBeTruthy()
      expect(es[clave]).not.toBe(en[clave])
    }
    expect(es.adminSettingsC2EdadMaxAyuda).toContain('108000')
    expect(en.adminSettingsC2EdadMaxAyuda).toContain('108000')
  })
})

describe('AdminSettings -- enfriamiento del freno de incertidumbre (#203)', () => {
  const CLAVE = 'proyectos.documentos.freno_incertidumbre_enfriamiento_s'
  const CON_FRENO = { data: {
    config: [{ key: 'system_name', value: 'Axioma' }, { key: CLAVE, value: '3600' }],
    limites: { ...LIMITES, [CLAVE]: { min: 60, max: 604800 } },
  } }

  it('el campo toma el valor y los límites del servidor', async () => {
    api.get.mockResolvedValue(CON_FRENO)
    renderSettings()
    const campo = await screen.findByLabelText(es.adminSettingsFrenoIncertidumbreEnfriamiento)
    expect(campo).toHaveValue(3600)
    expect(campo).toHaveAttribute('min', '60')
    expect(campo).toHaveAttribute('max', '604800')
  })

  it('guarda el valor con la clave de axioma_config', async () => {
    api.get.mockResolvedValue(CON_FRENO)
    api.put.mockResolvedValue({ data: { ok: true } })
    renderSettings()
    const campo = await screen.findByLabelText(es.adminSettingsFrenoIncertidumbreEnfriamiento)
    fireEvent.change(campo, { target: { value: '7200' } })
    await guardar()
    await waitFor(() => expect(api.put).toHaveBeenCalled())
    expect(api.put.mock.calls[0][1]).toContainEqual({ key: CLAVE, value: '7200' })
  })

  it('la etiqueta y la ayuda están traducidas en ambos idiomas', () => {
    for (const clave of ['adminSettingsFrenoIncertidumbreEnfriamiento',
      'adminSettingsFrenoIncertidumbreEnfriamientoAyuda']) {
      expect(es[clave], `es.${clave}`).toBeTruthy()
      expect(en[clave], `en.${clave}`).toBeTruthy()
      expect(es[clave]).not.toBe(en[clave])
    }
    // La ayuda es una funcion del minimo del servidor: no lleva el numero escrito a mano.
    expect(es.adminSettingsFrenoIncertidumbreEnfriamientoAyuda(75)).toContain('75')
    expect(en.adminSettingsFrenoIncertidumbreEnfriamientoAyuda(75)).toContain('75')
  })

  it('la ayuda muestra el minimo que manda el servidor (limites), no un numero fijo', async () => {
    api.get.mockResolvedValue({ data: {
      ...CON_FRENO.data,
      limites: { ...LIMITES, [CLAVE]: { min: 90, max: 604800 } },
    } })
    renderSettings()
    await screen.findByLabelText(es.adminSettingsFrenoIncertidumbreEnfriamiento)
    expect(screen.getByText(es.adminSettingsFrenoIncertidumbreEnfriamientoAyuda(90))).toBeInTheDocument()
  })
})
