import { render, screen } from '@testing-library/react'
import { describe, it, expect, beforeEach } from 'vitest'
import '@testing-library/jest-dom'
import HalEye from './HalEye'
import { I18nProvider } from '../../i18n/index.jsx'
import { useJaxStore } from '../../store/useJaxStore'

// Ruling 21 (fix-vivo-brief.md §C, decisión de Fernando 2026-09-14): HalEye
// gana una prop `reposo` que fuerza el estado de reposo del panel (azul,
// pulse-slow, sin etiqueta visible) SIN pasar por getEyeState ni la store --
// es lo que usa Login, que no tiene sesión. Sin la prop, el comportamiento
// dirigido por la store no cambia (lo usa LeftPanel).
function renderEye(props) {
  return render(<I18nProvider><HalEye {...props} /></I18nProvider>)
}

beforeEach(() => {
  // Cada test file arranca con el estado inicial real del módulo (facets
  // default, lasManos=false); estos tests fuerzan lo que necesitan encima.
  useJaxStore.setState({ killSwitchActive: false, lasManos: false })
})

describe('HalEye -- sin prop reposo, dirigido por la store', () => {
  it('con lasManos abajo (el default sin sesión), muestra el estado apagado, no reposo', () => {
    const { container } = renderEye({ size: 100 })
    expect(container.querySelector('.hal-anim-none')).toBeInTheDocument()
    expect(container.querySelector('.hal-anim-pulse-slow')).not.toBeInTheDocument()
    expect(screen.getByText('LAS MANOS DOWN')).toBeInTheDocument()
  })

  it('con killSwitchActive, sigue mostrando KILL SWITCH (comportamiento de siempre)', () => {
    useJaxStore.setState({ killSwitchActive: true })
    const { container } = renderEye({ size: 100 })
    expect(container.querySelector('.hal-anim-none')).toBeInTheDocument()
    expect(screen.getByText('KILL SWITCH')).toBeInTheDocument()
  })
})

// M3 (revisión de código, 2026-09-14): el anillo del housing usaba
// stroke-superficie sobre fill-fondo; la parte A del fix vivo intercambió
// --fondo/--superficie en claro y el anillo quedó casi invisible (241 245
// 249 vs 248 250 252). borde SÍ se distingue de fondo en los dos temas.
describe('HalEye -- anillo del housing', () => {
  it('el círculo exterior usa stroke-borde, no stroke-superficie', () => {
    const { container } = renderEye({ size: 100 })
    expect(container.querySelector('.stroke-borde')).toBeInTheDocument()
    expect(container.querySelector('.stroke-superficie')).not.toBeInTheDocument()
  })
})

describe('HalEye -- con prop reposo, fijo, ignora la store', () => {
  it('siempre pulse-slow y sin etiqueta visible, aunque la store diga otra cosa', () => {
    useJaxStore.setState({ killSwitchActive: true })
    const { container } = renderEye({ size: 100, reposo: true })
    expect(container.querySelector('.hal-anim-pulse-slow')).toBeInTheDocument()
    expect(container.querySelector('.hal-anim-none')).not.toBeInTheDocument()
    expect(screen.queryByText('KILL SWITCH')).not.toBeInTheDocument()
    expect(screen.queryByText('LAS MANOS DOWN')).not.toBeInTheDocument()
    expect(screen.queryByText('reposo')).not.toBeInTheDocument()
  })
})
