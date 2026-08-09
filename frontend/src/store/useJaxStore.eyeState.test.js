import { describe, expect, it } from 'vitest'
import { getEyeState } from './useJaxStore'

describe('getEyeState idle label i18n', () => {
  it('uses the given idleLabel instead of hardcoded Spanish when idle', () => {
    const eye = getEyeState({}, {}, true, false, false, 'idle')
    expect(eye.label).toBe('idle')
  })

  it('falls back to the Spanish default if no idleLabel is passed', () => {
    const eye = getEyeState({}, {}, true, false, false)
    expect(eye.label).toBe('reposo')
  })

  it('idleLabel is ignored for non-idle states (kill switch takes priority)', () => {
    const eye = getEyeState({}, {}, true, true, false, 'idle')
    expect(eye.label).toBe('KILL SWITCH')
  })
})
