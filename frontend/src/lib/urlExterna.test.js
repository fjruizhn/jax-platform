import { describe, expect, it } from 'vitest'
import { urlExternaSegura } from './urlExterna'

describe('urlExternaSegura', () => {
  it.each(['https://example.com/a', 'http://example.com/a?x=1'])('acepta URL absoluta %s', (url) => {
    expect(urlExternaSegura(url)).toBe(url)
  })

  it.each(['javascript:alert(1)', 'data:text/html,evil', 'file:///etc/passwd', '/ruta', '//example.com', '', null])(
    'rechaza protocolo o forma no permitida: %s', (url) => {
      expect(urlExternaSegura(url)).toBeNull()
    },
  )
})
