import { describe, it, expect } from 'vitest'
import { textoDeErrorDeMesa, textoDeAviso, textoDeViolacion, textoDeMotivoDeCosto } from './errores'
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
  it('estado_no_continuable usa el status si viene y un texto genérico si es null', () => {
    for (const d of [es, en]) {
      expect(d.erroresMesa.estado_no_continuable({ status: 'completed' })).toContain('completed')
      const generico = d.erroresMesa.estado_no_continuable({ status: null })
      expect(generico).not.toContain('null')
      expect(generico).not.toContain('undefined')
    }
    const texto = textoDeErrorDeMesa(es, err({ code: 'estado_no_continuable', status: null, mensaje: 'ya corre' }), 'G')
    expect(texto).toBe(`${es.erroresMesa.estado_no_continuable({ status: null })} ${es.respuestaDelServicio('ya corre')}`)
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
      expect(texto).toContain('2')
      expect(texto).toContain('3')
      expect(texto).toContain(d.detalleDelPrevuelo(`${d.detalleDePaso(detalle[0])}; ${d.detalleDePaso(detalle[1])}`))
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
