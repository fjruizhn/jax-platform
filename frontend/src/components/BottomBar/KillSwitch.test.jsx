import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import '@testing-library/jest-dom'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

// El rótulo del botón sale de i18n, no de un literal: con un valor centinela
// en el diccionario, el botón tiene que mostrar el centinela.
vi.mock('../../i18n/index.jsx', async () => {
  const real = await vi.importActual('../../i18n/es.js')
  return { useI18n: () => ({ t: { ...real.default, killButton: 'CENTINELA-KILL' } }) }
})

import KillSwitch from './KillSwitch'

describe('KillSwitch -- rótulo desde i18n', () => {
  it('muestra t.killButton', () => {
    render(<KillSwitch />)
    expect(screen.getByRole('button', { name: /CENTINELA-KILL/ })).toBeInTheDocument()
    expect(screen.queryByText(/^KILL$/)).not.toBeInTheDocument()
  })

  it('la clave existe en es.js y en.js', () => {
    expect(es.killButton).toBe('KILL')
    expect(en.killButton).toBe('KILL')
  })
})
