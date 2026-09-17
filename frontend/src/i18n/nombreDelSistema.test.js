import { describe, it, expect } from 'vitest'
import es from './es.js'
import en from './en.js'

// system_name (frente C, 2026-09-16): los textos que nombran al sistema lo
// reciben; ninguno lleva "Axioma" fijo.
describe('textos con el nombre del sistema', () => {
  it('loginButton, adminBack y smtpDesc interpolan el nombre en es y en; loginTitle ya no existe', () => {
    for (const t of [es, en]) {
      for (const clave of ['loginButton', 'adminBack', 'smtpDesc']) {
        expect(typeof t[clave], clave).toBe('function')
        expect(t[clave]('Hal'), clave).toContain('Hal')
        expect(t[clave]('Hal'), clave).not.toContain('Axioma')
      }
      expect(t.loginTitle).toBeUndefined()
    }
  })
})
