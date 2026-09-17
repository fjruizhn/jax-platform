import { describe, it, expect } from 'vitest'
import { textoDeErrorDeMesa, textoDeAviso, textoDeViolacion, textoDeMotivoDeCosto, textoDeCausa } from './errores'
import es from '../i18n/es.js'
import en from '../i18n/en.js'

const err = (detail) => ({ response: { data: { detail } } })

describe('textoDeErrorDeMesa (A-51)', () => {
  it('traduce un código con datos y agrega lo que respondió el servicio', () => {
    const texto = textoDeErrorDeMesa(es, err({ code: 'proveedor_error_http', facet: 'thot', status: 400, motivo: 'bad request' }), es.errorFacet)
    expect(texto).toBe(`${es.erroresMesa.proveedor_error_http({ facet: 'thot', status: 400 })} ${es.respuestaDelServicio('bad request')}`)
    expect(texto).not.toContain('proveedor_error_http')
  })

  it('un código como string también se traduce', () => {
    expect(textoDeErrorDeMesa(en, err('task_id_invalido'), en.errorTask)).toBe(en.erroresMesa.task_id_invalido({}))
  })

  it('el límite de pipelines usa el máximo del backend', () => {
    expect(textoDeErrorDeMesa(es, err({ code: 'limite_de_pipelines', max: 4 }), es.errorPipeline)).toContain('4')
  })

  // Frente C (2026-09-16): el máximo es el ajuste max_pipelines, no un 3 fijo;
  // el texto sale traducido en los dos idiomas con el número que mandó el backend.
  it('el límite de pipelines se traduce con el número del ajuste, en español y en inglés', () => {
    expect(textoDeErrorDeMesa(es, err({ code: 'limite_de_pipelines', max: 2 }), es.errorPipeline))
      .toBe('Ya hay 2 pipelines en curso: espera a que termine uno.')
    expect(textoDeErrorDeMesa(en, err({ code: 'limite_de_pipelines', max: 1 }), en.errorPipeline))
      .toBe('1 pipelines are already running: wait for one to finish.')
  })

  it('un código desconocido o un texto libre caen al genérico, nunca crudo', () => {
    expect(textoDeErrorDeMesa(es, err({ code: 'otro_codigo' }), es.errorFacet)).toBe(es.errorFacet)
    expect(textoDeErrorDeMesa(es, err('Límite de 3 pipelines concurrentes alcanzado'), es.errorPipeline)).toBe(es.errorPipeline)
    expect(textoDeErrorDeMesa(es, {}, es.errorImagen)).toBe(es.errorImagen)
  })
})

describe('textoDeAviso (A-53)', () => {
  it('identidad del modelo arma el hosting por proveedor, en cada idioma', () => {
    const aviso = { code: 'identidad_del_modelo', params: { facet: 'jax_local', model: 'qwen', provider: 'ollama' } }
    expect(textoDeAviso(es, aviso)).toBe(es.avisosChat.identidad_del_modelo(aviso.params, es.hostingDeProveedor.ollama))
    expect(textoDeAviso(en, aviso)).toContain('qwen')
    expect(textoDeAviso(en, aviso)).not.toBe(textoDeAviso(es, aviso))
  })

  it('un proveedor sin texto usa el hosting genérico; un código desconocido, el aviso genérico', () => {
    expect(textoDeAviso(es, { code: 'identidad_del_modelo', params: { model: 'm', provider: 'nuevo' } })).toContain(es.hostingGenerico)
    expect(textoDeAviso(es, { code: 'algo_nuevo', params: {} })).toBe(es.avisoDesconocido)
  })
})

describe('códigos que coinciden con propiedades de Object (ronda final M4)', () => {
  it.each(['constructor', 'toString', '__proto__', 'hasOwnProperty'])('%s no se trata como un código conocido', (code) => {
    expect(textoDeErrorDeMesa(es, err({ code }), es.errorFacet)).toBe(es.errorFacet)
    expect(textoDeErrorDeMesa(es, err(code), es.errorFacet)).toBe(es.errorFacet)
    expect(textoDeAviso(es, { code, params: {} })).toBe(es.avisoDesconocido)
  })

  it('un proveedor "constructor" usa el hosting genérico', () => {
    const aviso = { code: 'identidad_del_modelo', params: { provider: 'constructor' } }
    expect(textoDeAviso(es, aviso)).toBe(es.avisosChat.identidad_del_modelo(aviso.params, es.hostingGenerico))
  })
})

describe('pre-vuelo y continuar (spec 2026-09-17)', () => {
  const CODIGOS = ['prevuelo_rechazado', 'confirmacion_de_costo', 'costo_supera_lo_aceptado', 'reasignacion_invalida',
    'prevuelo_no_disponible', 'estado_no_continuable', 'pasos_requeridos', 'costo_confirmado_invalido',
    // Adenda Task 8 ítem 4 (enmienda 3) y el motivo kill_switch de continue/preflight.
    'limite_de_activos', 'plan_rechazado', 'plan_inconsistente', 'no_existe', 'kill_switch']

  it('cada código nuevo se traduce en los dos idiomas y no sale crudo', () => {
    for (const d of [es, en]) {
      for (const code of CODIGOS) {
        const texto = textoDeErrorDeMesa(d, err({ code, violaciones: [] }), 'GENERICO')
        expect(texto, code).not.toBe('GENERICO')
        expect(texto, code).not.toContain(code)
      }
    }
  })

  it('una violación se lee por su regla, con el paso humano y el detalle como dato', () => {
    const v = { paso: 4, faceta: 'kimi', regla: 'tope_insuficiente', detalle: 'tope 8000 < 16384' }
    expect(textoDeViolacion(es, v)).toBe(`${es.reglasPrevuelo.tope_insuficiente(v)} ${es.detalleDelPrevuelo('tope 8000 < 16384')}`)
    expect(es.reglasPrevuelo.tope_insuficiente(v)).toContain('5')
    for (const regla of ['tope_insuficiente', 'sin_contrato_de_salida', 'credencial_ausente', 'faceta_caida', 'faceta_inexistente']) {
      expect(en.reglasPrevuelo[regla](v), regla).toContain('kimi')
    }
  })

  it('una regla desconocida cae en el texto genérico de regla, nunca cruda', () => {
    const v = { paso: 0, faceta: 'x', regla: 'constructor' }
    expect(textoDeViolacion(es, v)).toBe(es.reglaPrevueloDesconocida(v))
    expect(textoDeViolacion(es, v)).not.toContain('constructor')
  })

  // Adenda ítem 4 / enmienda 2: estado_no_continuable trae {status, mensaje}.
  // Fix round 1 ítems 1-2: el status nunca sale crudo; se traduce con la tabla
  // de etiquetas y el texto cambia según el estado.
  const noContinuable = (d, status, mensaje) =>
    textoDeErrorDeMesa(d, err({ code: 'estado_no_continuable', status, mensaje }), 'G')

  it('un status conocido se muestra con su etiqueta, nunca crudo, y sin el mensaje', () => {
    for (const d of [es, en]) {
      for (const status of ['completed', 'failed', 'running']) {
        const texto = noContinuable(d, status, 'texto de jacobs')
        expect(texto, status).toContain(d.pipelineStatusLabels[status])
        expect(texto, status).not.toContain(status)
        expect(texto, status).not.toContain('texto de jacobs')
      }
    }
  })

  it('interrupted manda a reanudar, sin el mensaje', () => {
    expect(noContinuable(es, 'interrupted', 'x-msg')).toMatch(/reanud/i)
    expect(noContinuable(en, 'interrupted', 'x-msg')).toMatch(/resum/i)
    for (const d of [es, en]) {
      expect(noContinuable(d, 'interrupted', 'x-msg')).not.toContain('interrupted')
      expect(noContinuable(d, 'interrupted', 'x-msg')).not.toContain('x-msg')
    }
  })

  it('status null: otro pedido cambió el pipeline, recargar; con el mensaje como dato', () => {
    expect(noContinuable(es, null)).toMatch(/recarg/i)
    expect(noContinuable(en, null)).toMatch(/reload/i)
    for (const d of [es, en]) {
      expect(noContinuable(d, null, 'ya corre')).toBe(`${d.erroresMesa.estado_no_continuable({ status: null })} ${d.respuestaDelServicio('ya corre')}`)
      expect(noContinuable(d, null)).not.toContain('null')
    }
  })

  it('un status desconocido o heredado cae en el genérico, sin crudo, con el mensaje', () => {
    for (const d of [es, en]) {
      for (const status of ['zzz_raro', 'constructor', 7]) {
        const texto = noContinuable(d, status, 'dato')
        expect(texto).toBe(`${d.erroresMesa.estado_no_continuable({ status: 'zzz_raro' })} ${d.respuestaDelServicio('dato')}`)
        expect(texto).not.toContain(String(status))
      }
      const textos = new Set([null, 'interrupted', 'completed', 'zzz_raro'].map(s => d.erroresMesa.estado_no_continuable({ status: s })))
      expect(textos.size).toBe(4)
    }
  })

  // Adenda ítem 3: `detalle` string o lista normalizada, siempre como dato.
  it('un detalle de texto se muestra como dato', () => {
    const texto = textoDeErrorDeMesa(es, err({ code: 'limite_de_activos', detalle: 'tope global 8' }), 'G')
    expect(texto).toBe(`${es.erroresMesa.limite_de_activos({})} ${es.detalleDelPrevuelo('tope global 8')}`)
  })

  it('un detalle en lista se lee paso por paso, nunca como objeto crudo', () => {
    for (const d of [es, en]) {
      const detalle = [{ paso: 1, faceta: 'ada', motivo: 'no existe' }, { paso: 2, faceta: null, motivo: 'clean-room' }]
      const texto = textoDeErrorDeMesa(d, err({ code: 'reasignacion_invalida', violaciones: [], detalle }), 'G')
      expect(texto).not.toContain('[object Object]')
      expect(texto).not.toContain('null')
      expect(texto).toContain('ada')
      expect(texto).toContain('no existe')
      expect(texto).toContain('clean-room')
      expect(texto).toContain(d.detalleDelPrevuelo(`${d.detalleDePaso(detalle[0])}; ${d.detalleDePaso(detalle[1])}`))
    }
  })

  // Fix round 1 ítem 3: posición y faceta sólo si tienen la forma esperada.
  it('un paso sin posición ni faceta válidas usa el texto genérico y omite el motivo vacío', () => {
    expect(es.detalleDePaso({ paso: null, faceta: null, motivo: '' })).toBe(es.unPaso)
    expect(en.detalleDePaso({ paso: null, faceta: null, motivo: '' })).toBe(en.unPaso)
    expect(es.detalleDePaso({ paso: '3x', faceta: 'ada', motivo: 'm' })).toBe(`${es.unPaso} (ada): m`)
    expect(es.detalleDePaso({ paso: 1, faceta: { a: 1 }, motivo: 'm' })).toBe('Paso 2: m')
    expect(en.detalleDePaso({ paso: '3', faceta: 'ada', motivo: 'm' })).toBe('Step 4 (ada): m')
    expect(en.detalleDePaso({ paso: 0, faceta: 'ada', motivo: 5 })).toBe('Step 1 (ada)')
    for (const d of [es, en]) {
      const texto = d.reglaPrevueloDesconocida({ paso: '3x', faceta: { a: 1 } })
      expect(texto.startsWith(`${d.unPaso}:`)).toBe(true)
      expect(texto).not.toContain('[object Object]')
      expect(texto).not.toContain('3x')
    }
  })

  it('un detalle de forma rara (objeto, lista vacía) no se muestra', () => {
    const base = es.erroresMesa.plan_rechazado({})
    expect(textoDeErrorDeMesa(es, err({ code: 'plan_rechazado', detalle: { x: 1 } }), 'G')).toBe(base)
    expect(textoDeErrorDeMesa(es, err({ code: 'plan_rechazado', detalle: [] }), 'G')).toBe(base)
  })
})

describe('textoDeMotivoDeCosto (adenda Task 8 ítem 6)', () => {
  const MOTIVOS = ['acotado', 'sin_precio', 'local', 'suscripcion', 'mecanico', 'sin_contrato_de_salida',
    'faceta_inexistente', 'herramientas_sin_tope']

  it('cada motivo conocido se traduce en los dos idiomas', () => {
    for (const d of [es, en]) {
      for (const m of MOTIVOS) {
        const texto = textoDeMotivoDeCosto(d, m)
        expect(texto, m).toBe(d.motivosDeCosto[m])
        expect(texto, m).not.toBe(d.motivoDeCostoDesconocido)
        expect(texto, m).not.toBe(m)
      }
    }
  })

  it('un motivo desconocido, heredado o null cae en el texto genérico', () => {
    for (const malo of ['otro', 'constructor', null, undefined, 3]) {
      expect(textoDeMotivoDeCosto(es, malo)).toBe(es.motivoDeCostoDesconocido)
    }
  })
})

describe('causasDeAborto (spec 2026-09-17)', () => {
  it('cada causa tiene texto en los dos idiomas y el fallo nombra el paso humano', () => {
    for (const d of [es, en]) {
      for (const tipo of ['fallo', 'cancelado', 'kill_switch', 'expirado', 'desconocida']) {
        const texto = d.causasDeAborto[tipo]({ tipo })
        expect(texto, tipo).toBeTruthy()
        expect(texto, tipo).not.toContain(tipo)
      }
      expect(d.causasDeAborto.fallo({ tipo: 'fallo', paso: 2 })).toContain('3')
    }
  })
})

// Fix round 1 ítem 7: la causa del aborto se lee por su tipo, nunca cruda.
describe('textoDeCausa', () => {
  it('una causa conocida usa su texto y el fallo nombra el paso', () => {
    for (const d of [es, en]) {
      expect(textoDeCausa(d, { tipo: 'fallo', paso: 2 })).toBe(d.causasDeAborto.fallo({ paso: 2 }))
      expect(textoDeCausa(d, { tipo: 'expirado' })).toBe(d.causasDeAborto.expirado({}))
    }
  })

  it('tipo desconocido, heredado, no string o causa null cae en desconocida', () => {
    for (const causa of [{ tipo: 'otro' }, { tipo: 'constructor' }, { tipo: 5 }, null, undefined, 'fallo']) {
      expect(textoDeCausa(es, causa)).toBe(es.causasDeAborto.desconocida({}))
    }
  })

  it('un paso no entero en un fallo no se inventa', () => {
    expect(textoDeCausa(es, { tipo: 'fallo', paso: '2' })).toBe(es.causasDeAborto.fallo({}))
    expect(textoDeCausa(es, { tipo: 'fallo', paso: { x: 1 } })).not.toContain('[object Object]')
  })
})

// Fix round 2 ítems 2-3: sólo un string no vacío se agrega como dato.
describe('datos del servicio con forma rara', () => {
  it('un motivo que no es string no se agrega a textoDeErrorDeMesa', () => {
    for (const motivo of [{ x: 1 }, ['a'], 5, true, '']) {
      const texto = textoDeErrorDeMesa(es, err({ code: 'jacobs_no_responde', motivo }), 'G')
      expect(texto).toBe(es.erroresMesa.jacobs_no_responde({}))
      expect(texto).not.toContain('[object Object]')
    }
  })

  it('un detalle de violación que no es string no se agrega a textoDeViolacion', () => {
    const v = { paso: 0, faceta: 'ada', regla: 'faceta_caida' }
    for (const detalle of [{ x: 1 }, ['a'], 5, true, '']) {
      const texto = textoDeViolacion(es, { ...v, detalle })
      expect(texto).toBe(es.reglasPrevuelo.faceta_caida(v))
      expect(texto).not.toContain('[object Object]')
    }
  })
})
