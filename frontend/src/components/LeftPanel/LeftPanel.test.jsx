import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import '@testing-library/jest-dom'
import es from '../../i18n/es.js'
import en from '../../i18n/en.js'

// El rótulo del bloque de estado de LAS MANOS sale de i18n, no de un
// literal: con un valor centinela en el diccionario, el panel tiene que
// mostrar el centinela. Ruling 35 (controller): la clave `lasManos` no
// existía en es.js ni en.js -- el rótulo se renderizaba en blanco.
vi.mock('../../i18n/index.jsx', async () => {
  const real = await vi.importActual('../../i18n/index.jsx')
  return {
    ...real,
    useI18n: () => ({ t: { ...es, lasManos: 'CENTINELA-LAS-MANOS' } }),
  }
})

import LeftPanel from './LeftPanel'

describe('LeftPanel -- rótulo de LAS MANOS desde i18n', () => {
  it('muestra t.lasManos', () => {
    render(<LeftPanel />)
    expect(screen.getByText('CENTINELA-LAS-MANOS')).toBeInTheDocument()
  })

  it('la clave existe en es.js y en.js con el nombre propio del componente', () => {
    expect(es.lasManos).toBe('LAS MANOS')
    expect(en.lasManos).toBe('LAS MANOS')
  })
})

// M-2 (revisión final PR 3, 2026-09-14): wsStatus (useJaxStore.js,
// api/websocket.js) se mostraba crudo ('connected'/'disconnected'/
// 'reconnecting'), sin traducir.
describe('LeftPanel -- wsStatus traducido (M-2)', () => {
  it('las claves de los 3 estados existen en es y en', () => {
    for (const clave of ['connected', 'disconnected', 'reconnecting']) {
      expect(es.wsStatusLabels[clave], `es.wsStatusLabels.${clave}`).toBeTruthy()
      expect(en.wsStatusLabels[clave], `en.wsStatusLabels.${clave}`).toBeTruthy()
    }
  })

  it('el estado por defecto (disconnected) se muestra traducido, no el valor crudo', () => {
    render(<LeftPanel />)
    expect(screen.getByText(es.wsStatusLabels.disconnected)).toBeInTheDocument()
    expect(screen.queryByText('disconnected')).not.toBeInTheDocument()
  })
})
