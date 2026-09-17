import { describe, it, expect, vi, beforeEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { renderHook } from '@testing-library/react'
import es from './i18n/es.js'
import en from './i18n/en.js'
import { diccionarioActivo } from './i18n/index.jsx'
import { createWebSocket } from './api/websocket'
import { useWebSocket } from './store/useWebSocket'
import { getEyeState } from './store/useJaxStore'

const fuente = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8')

// Las 25 claves sin lector (anexo A, ficha 16; verificadas una por una).
const MUERTAS = ['adminCostsPeriod', 'adminEventsTitle', 'adminKeyAddModel', 'adminKeyFacet', 'adminKeyModel',
  'adminKeyModelAdd', 'adminKeyModelDelete', 'adminKeyModelDeleteActive', 'adminKeyModelDeleteConfirmButton',
  'adminKeyModelDeleteConfirmPlaceholder', 'adminKeyModelDeleteConfirmTitle', 'adminKeyModelDeleteConfirmWrong',
  'adminKeyModelDeleteConfirmSum', 'adminKeyModelName', 'adminKeyModelProvider', 'adminNav', 'adminProposalsCurrent',
  'adminSettingsWsNotif', 'adminUserChangeRole', 'adminUserResetPwd', 'attachedFile', 'attachFile', 'attachTooLarge',
  'attachTypes', 'statApiKeys']

const ETIQUETAS = { reposo: 'r', killSwitch: 'k', dalle: 'd', lasManosDown: 'l', gate: 'g', jacobs: 'j' }

beforeEach(() => localStorage.clear())

describe('limpieza del frontend (frente A)', () => {
  it('A-01: @heroicons/react no es dependencia', () => {
    expect(JSON.parse(fuente('../package.json')).dependencies).not.toHaveProperty('@heroicons/react')
  })

  it('A-09: createWebSocket solo devuelve close y no instala un onerror vacío', () => {
    class FakeWS { constructor() { FakeWS.ultimo = this } close() {} }
    vi.stubGlobal('WebSocket', FakeWS)
    const ws = createWebSocket('5', 'tok', () => {}, () => {})
    expect(Object.keys(ws)).toEqual(['close'])
    expect(FakeWS.ultimo.onerror).toBeUndefined()
    ws.close()
    vi.unstubAllGlobals()
  })

  it('A-09: useWebSocket no devuelve nada', () => {
    const { result } = renderHook(() => useWebSocket())
    expect(result.current).toBeUndefined()
  })

  it('A-10: Admin no pide i18n que no usa', () => {
    expect(fuente('./pages/Admin.jsx')).not.toContain('useI18n')
  })

  it('A-11/A-17: las claves sin lector no existen en ningún idioma', () => {
    for (const clave of MUERTAS) {
      expect(es).not.toHaveProperty(clave)
      expect(en).not.toHaveProperty(clave)
    }
  })

  it('A-29: el store usa el diccionario activo del proveedor de i18n', () => {
    localStorage.setItem('jax_lang', 'en')
    expect(diccionarioActivo()).toBe(en)
    localStorage.setItem('jax_lang', 'xx')
    expect(diccionarioActivo()).toBe(es)
    expect(fuente('./store/useJaxStore.js')).not.toMatch(/function _t\(/)
  })

  it('A-45: getEyeState exige todas las etiquetas', () => {
    expect(() => getEyeState({}, {}, true, false, false)).toThrow(/etiqueta/)
    const { killSwitch, ...sinUna } = ETIQUETAS
    expect(() => getEyeState({}, {}, true, true, false, sinUna)).toThrow(/killSwitch/)
    expect(getEyeState({}, {}, true, true, false, ETIQUETAS).label).toBe('k')
    expect(fuente('./store/useJaxStore.js')).not.toContain("'KILL SWITCH'")
  })
})
