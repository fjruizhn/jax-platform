import { describe, it, expect } from 'vitest'
import { textoDeErrorDeMesa, textoDeAviso } from './errores'
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
