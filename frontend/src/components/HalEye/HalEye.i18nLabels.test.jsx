import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import '@testing-library/jest-dom'

// M5 (revisión de código, 2026-09-14, fix vivo): confirma que HalEye REALMENTE
// lee las etiquetas de i18n (t.eye*) y se las pasa a getEyeState, en vez de
// que getEyeState las traiga hardcodeadas y HalEye sólo mire. Con un t de
// mentira, de valores distintos a los de producción, la prueba es real: si
// HalEye ignorara t y getEyeState siguiera con el texto de siempre, estos
// tests verían "KILL SWITCH"/"LAS MANOS DOWN"/etc en vez de los valores de
// prueba -- a diferencia de probar con el t real, donde es/en dan el mismo
// texto (son nombres propios/técnicos, ver i18n/es.js y en.js) y no
// distinguirían "vino de i18n" de "quedó hardcodeado por casualidad".
vi.mock('../../i18n/index.jsx', () => ({
  useI18n: () => ({
    t: {
      eyeIdle: 'reposo-test',
      eyeKillSwitch: 'INTERRUPTOR-TEST',
      eyeDallE3: 'PINTOR-TEST',
      eyeLasManosDown: 'MANOS-TEST',
      eyeGate: 'PORTON-TEST',
      eyeJacobs: 'Jacobo-TEST',
      halEyeAriaLabel: (label) => `Ojo HAL -- ${label}`,
    },
  }),
}))

import HalEye from './HalEye'
import { useJaxStore } from '../../store/useJaxStore'

beforeEach(() => {
  useJaxStore.setState({
    killSwitchActive: false, lasManos: false, generatingImage: false, activePipelines: {},
  })
})

describe('HalEye -- las etiquetas de getEyeState salen de i18n, no hardcodeadas (M5)', () => {
  it('KILL SWITCH', () => {
    useJaxStore.setState({ killSwitchActive: true })
    render(<HalEye size={100} />)
    expect(screen.getByText('INTERRUPTOR-TEST')).toBeInTheDocument()
  })

  it('LAS MANOS DOWN', () => {
    render(<HalEye size={100} />)
    expect(screen.getByText('MANOS-TEST')).toBeInTheDocument()
  })

  it('DALL-E 3', () => {
    useJaxStore.setState({ lasManos: true, generatingImage: true })
    render(<HalEye size={100} />)
    expect(screen.getByText('PINTOR-TEST')).toBeInTheDocument()
  })

  it('GATE', () => {
    useJaxStore.setState({ lasManos: true, activePipelines: { p: { status: 'waiting_gate' } } })
    render(<HalEye size={100} />)
    expect(screen.getByText('PORTON-TEST')).toBeInTheDocument()
  })

  it('Jacobs', () => {
    useJaxStore.setState({ lasManos: true, activePipelines: { p: { status: 'running' } } })
    render(<HalEye size={100} />)
    expect(screen.getByText('Jacobo-TEST')).toBeInTheDocument()
  })
})
