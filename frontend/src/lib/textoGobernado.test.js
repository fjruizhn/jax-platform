import { describe, it, expect } from 'vitest'
import { decodificarTextoGobernado, avisoGobernadoDe } from './textoGobernado'

describe('decodificarTextoGobernado', () => {
  it('deshace las cinco entidades que produce html.escape(quote=True)', () => {
    expect(decodificarTextoGobernado('it&#x27;s &quot;ok&quot; &amp; &lt;b&gt;'))
      .toBe('it\'s "ok" & <b>')
  })

  it('decodifica una sola vez: &amp;lt; es el texto literal &lt;', () => {
    expect(decodificarTextoGobernado('&amp;lt;script&amp;gt;')).toBe('&lt;script&gt;')
  })

  it('no decodifica nada fuera de las cinco: ni numericas ni nombradas', () => {
    const s = '&#60; &#x3c; &nbsp; &copy; &apos; &#39;'
    expect(decodificarTextoGobernado(s)).toBe(s)
  })

  it('deja intacto el texto sin entidades, saltos de linea incluidos', () => {
    expect(decodificarTextoGobernado('hola\n\tmundo')).toBe('hola\n\tmundo')
  })

  it('tolera valores no string', () => {
    expect(decodificarTextoGobernado(undefined)).toBe('')
    expect(decodificarTextoGobernado(null)).toBe('')
  })
})

describe('avisoGobernadoDe', () => {
  it('reconoce el aviso degradado por estado de contrato, no por texto', () => {
    expect(avisoGobernadoDe({ governed_plain: true, contract_state: 'DEGRADED_STRUCTURED' }))
      .toBe('respuesta_no_verificable')
    expect(avisoGobernadoDe({ governed_plain: true, contract_state: 'UNAVAILABLE' }))
      .toBe('respuesta_no_verificable')
  })

  it('no marca respuestas validas ni no gobernadas', () => {
    expect(avisoGobernadoDe({ governed_plain: true, contract_state: 'VALID' })).toBeNull()
    expect(avisoGobernadoDe({ governed_plain: true, contract_state: 'BLOCKED_SYSTEM_CLAIM' })).toBeNull()
    expect(avisoGobernadoDe({ governed_plain: false, contract_state: 'UNAVAILABLE' })).toBeNull()
    expect(avisoGobernadoDe({})).toBeNull()
  })
})
