import { beforeEach, describe, expect, it, vi } from 'vitest'

// El módulo captura VITE_OFFICE_HOSTS al cargarse: cada caso reimporta con su
// propio entorno para probar la lectura real, no un valor inyectado.
const cargar = async () => {
  vi.resetModules()
  const modulo = await import('./instalacionOficina')
  return modulo.esInstalacionDeOficina
}

describe('esInstalacionDeOficina', () => {
  beforeEach(() => { vi.unstubAllEnvs() })

  it('sin VITE_OFFICE_HOSTS ningún host es instalación de oficina (comportamiento Axioma)', async () => {
    vi.stubEnv('VITE_OFFICE_HOSTS', '')
    const es = await cargar()
    expect(es('virtualoffice.axioma-ia.io')).toBe(false)
    expect(es('axioma-ia.io')).toBe(false)
  })

  it('matchea por host exacto, tolerando mayúsculas y espacios en la lista', async () => {
    vi.stubEnv('VITE_OFFICE_HOSTS', ' Office.Test , otro.test ')
    const es = await cargar()
    expect(es('office.test')).toBe(true)
    expect(es('OFFICE.TEST')).toBe(true)
    expect(es('otro.test')).toBe(true)
  })

  it('un host que contiene al de la oficina, sin serlo, no hereda el modo oficina', async () => {
    vi.stubEnv('VITE_OFFICE_HOSTS', 'office.test')
    const es = await cargar()
    expect(es('evil-office.test')).toBe(false)
    expect(es('office.test.evil.com')).toBe(false)
    expect(es('office.test.evil.com'.slice(0, 10))).toBe(false)
  })
})
