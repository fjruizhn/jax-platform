import { describe, it, expect } from 'vitest'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'
import { textoDeErrorDeMesa } from '../../api/errores'
import { cuerpoDeAdjunto, vistaDeAdjunto, faltaSoporteDeImagen } from './adjuntos'

const IMAGEN = { tipo: 'imagen', nombre: 'f.png', mime: 'image/png', bytes: 3, base64: 'QUJD' }
const TEXTO = { tipo: 'texto', origen: 'pdf', nombre: 'i.pdf', bytes: 9, contenido: 'ventas', recortado: true }

const err = (detail) => ({ response: { data: { detail } } })

describe('adjuntos -- contrato con /api/chat', () => {
  it('imagen: solo tipo, nombre, mime y base64', () => {
    expect(cuerpoDeAdjunto(IMAGEN)).toEqual({ tipo: 'imagen', nombre: 'f.png', mime: 'image/png', base64: 'QUJD' })
  })
  it('texto: solo tipo, origen, nombre y contenido (extra=forbid en el servidor)', () => {
    expect(cuerpoDeAdjunto(TEXTO)).toEqual({ tipo: 'texto', origen: 'pdf', nombre: 'i.pdf', contenido: 'ventas' })
  })
  it('la vista para el mensaje usa la forma que ya lee Message.jsx', () => {
    expect(vistaDeAdjunto(IMAGEN)).toEqual({ type: 'image', filename: 'f.png', base64: 'data:image/png;base64,QUJD' })
    expect(vistaDeAdjunto(TEXTO)).toEqual({ type: 'text', filename: 'i.pdf' })
  })
  it('los códigos de adjuntos se traducen vía erroresMesa/textoDeErrorDeMesa, con y sin datos', () => {
    expect(textoDeErrorDeMesa(es, err({ code: 'adjunto_demasiado_grande', max_bytes: 10485760 }), 'x'))
      .toBe('El archivo supera el máximo de 10 MB.')
    expect(textoDeErrorDeMesa(en, err({ code: 'imagen_no_soportada', facet: 'jekyll' }), 'x'))
      .toBe(en.erroresMesa.imagen_no_soportada())
    expect(textoDeErrorDeMesa(es, err('faceta desconocida'), 'generico')).toBe('generico')
    expect(textoDeErrorDeMesa(es, {}, 'generico')).toBe('generico')
  })
  it('adjuntos_demasiados concuerda en número con el máximo', () => {
    expect(es.erroresMesa.adjuntos_demasiados({ max: 1 })).toBe('Se puede adjuntar hasta 1 archivo por mensaje.')
    expect(es.erroresMesa.adjuntos_demasiados({ max: 3 })).toBe('Se pueden adjuntar hasta 3 archivos por mensaje.')
    expect(en.erroresMesa.adjuntos_demasiados({ max: 1 })).toBe('You can attach up to 1 file per message.')
    expect(en.erroresMesa.adjuntos_demasiados({ max: 3 })).toBe('You can attach up to 3 files per message.')
  })
  it('falta soporte solo con imagen y faceta fuera de la lista', () => {
    const politica = { facetas_con_imagen: ['hipatia'] }
    expect(faltaSoporteDeImagen(IMAGEN, politica, 'jekyll')).toBe(true)
    expect(faltaSoporteDeImagen(IMAGEN, politica, 'hipatia')).toBe(false)
    expect(faltaSoporteDeImagen(TEXTO, politica, 'jekyll')).toBe(false)
    expect(faltaSoporteDeImagen(IMAGEN, null, 'hipatia')).toBe(true)
  })
  it('las claves nuevas de adjuntos existen en es y en (parity de erroresMesa la cubre paridad.test.js)', () => {
    for (const k of ['adjuntoPoliticaNoDisponible', 'adjuntoImagenSinSoporte', 'adjuntoRecortado']) {
      expect(es[k]).toBeTruthy()
      expect(en[k]).toBeTruthy()
    }
  })
})
