import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'
import { textoDeErrorDeMesa } from '../../api/errores'
import { cuerpoDeAdjunto, vistaDeAdjunto, faltaSoporteDeImagen } from './adjuntos'

// RD4 (2026-09-17): /api/chat/upload devuelve {id, tipo, nombre, bytes, ...} sin
// base64 ni texto completo (docs/superpowers/specs/2026-09-17-frente-d-adjuntos-por-referencia.md).
// El compositor guarda el File elegido junto a esa respuesta.
const IMAGEN = {
  id: 'abcDEF012345abcDEF012345abcDEF0', tipo: 'imagen', nombre: 'f.png', mime: 'image/png', bytes: 3,
  archivo: new File(['x'], 'f.png', { type: 'image/png' }),
}
const TEXTO = {
  id: 'ghiJKL678901ghiJKL678901ghiJKL6', tipo: 'texto', origen: 'pdf', nombre: 'i.pdf', bytes: 9,
  caracteres: 6, recortado: true, vista_previa: 'ventas',
}

const err = (detail) => ({ response: { data: { detail } } })

describe('adjuntos -- contrato con /api/chat por id (RD4)', () => {
  it('cuerpoDeAdjunto manda solo el id, nunca bytes ni contenido', () => {
    expect(cuerpoDeAdjunto(IMAGEN)).toEqual({ id: IMAGEN.id })
    expect(cuerpoDeAdjunto(TEXTO)).toEqual({ id: TEXTO.id })
  })

  describe('vistaDeAdjunto -- object URL propio del mensaje enviado', () => {
    beforeEach(() => {
      let n = 0
      vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => `blob:mensaje-${++n}`), revokeObjectURL: vi.fn() })
    })
    afterEach(() => vi.unstubAllGlobals())

    it('imagen: crea un object URL a partir del File guardado (forma que Message.jsx ya lee)', () => {
      expect(vistaDeAdjunto(IMAGEN)).toEqual({ type: 'image', filename: 'f.png', base64: 'blob:mensaje-1' })
      expect(URL.createObjectURL).toHaveBeenCalledWith(IMAGEN.archivo)
    })

    it('cada llamada obtiene su PROPIO object URL -- no reusa el del compositor', () => {
      const a = vistaDeAdjunto(IMAGEN)
      const b = vistaDeAdjunto(IMAGEN)
      expect(a.base64).not.toBe(b.base64)
      expect(URL.createObjectURL).toHaveBeenCalledTimes(2)
    })

    it('texto: solo type y filename, sin base64 ni contenido', () => {
      expect(vistaDeAdjunto(TEXTO)).toEqual({ type: 'text', filename: 'i.pdf' })
      expect(URL.createObjectURL).not.toHaveBeenCalled()
    })
  })

  it('los códigos de adjuntos se traducen vía erroresMesa/textoDeErrorDeMesa, con y sin datos', () => {
    expect(textoDeErrorDeMesa(es, err({ code: 'adjunto_demasiado_grande', max_bytes: 10485760 }), 'x'))
      .toBe('El archivo supera el máximo de 10 MB.')
    expect(textoDeErrorDeMesa(en, err({ code: 'imagen_no_soportada', facet: 'jekyll' }), 'x'))
      .toBe(en.erroresMesa.imagen_no_soportada())
    expect(textoDeErrorDeMesa(es, err({ code: 'adjunto_no_encontrado' }), 'x'))
      .toBe(es.erroresMesa.adjunto_no_encontrado())
    expect(textoDeErrorDeMesa(es, err('faceta desconocida'), 'generico')).toBe('generico')
    expect(textoDeErrorDeMesa(es, {}, 'generico')).toBe('generico')
  })

  it('cuota, disco y límite de subidas (RD6/RD7) se traducen con sus datos, en es y en', () => {
    expect(textoDeErrorDeMesa(es, err({ code: 'adjuntos_cuota_excedida', cuota_bytes: 524288000 }), 'x'))
      .toBe('Llegaste al máximo de 500 MB de adjuntos guardados. Los adjuntos vencen solos: vuelve a intentarlo más tarde.')
    expect(textoDeErrorDeMesa(en, err({ code: 'adjuntos_cuota_excedida', cuota_bytes: 524288000 }), 'x'))
      .toBe('You reached the 500 MB limit of stored attachments. Attachments expire on their own: try again later.')
    expect(textoDeErrorDeMesa(es, err({ code: 'adjuntos_sin_espacio' }), 'x')).toBe(es.erroresMesa.adjuntos_sin_espacio())
    expect(textoDeErrorDeMesa(en, err({ code: 'adjuntos_sin_espacio' }), 'x')).toBe(en.erroresMesa.adjuntos_sin_espacio())
    expect(textoDeErrorDeMesa(es, err({ code: 'adjuntos_subidas_limite', retry_after: 35 }), 'x'))
      .toBe('Subiste demasiados archivos seguidos. Espera 35 s y vuelve a intentarlo.')
    expect(textoDeErrorDeMesa(en, err({ code: 'adjuntos_subidas_limite', retry_after: 35 }), 'x'))
      .toBe('Too many uploads in a row. Wait 35 s and try again.')
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

  it('las claves nuevas de adjuntos existen en es y en (paridad de erroresMesa la cubre paridad.test.js)', () => {
    for (const k of ['adjuntoPoliticaNoDisponible', 'adjuntoImagenSinSoporte', 'adjuntoRecortado']) {
      expect(es[k]).toBeTruthy()
      expect(en[k]).toBeTruthy()
    }
  })
})
