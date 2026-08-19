import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import '@testing-library/jest-dom'
import PipelineModal from './PipelineModal'
import { I18nProvider } from '../../i18n/index.jsx'

// R4 -- el picker de motor (kimi/jax_local) se puebla desde
// GET /api/motors/capabilities. Sin datos aun cargados (o si el fetch
// falla) el <select> simplemente no aparece -- no debe romper el modal.
function renderModal(props = {}) {
  return render(
    <I18nProvider>
      <PipelineModal
        objective="probar el picker de motor"
        onClose={() => {}}
        onSubmit={() => Promise.resolve()}
        {...props}
      />
    </I18nProvider>
  )
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(() =>
    Promise.resolve({
      ok: true,
      json: () => Promise.resolve({
        capabilities: [
          { key: 'implementation', allowed_motors: ['kimi'] },
          { key: 'generate', allowed_motors: ['kimi', 'ada', 'jax_local'] },
        ],
      }),
    })
  ))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('PipelineModal -- picker de motor (R4)', () => {
  it('muestra el <select> de motor para Kimi tras seleccionarlo, poblado desde /api/motors/capabilities', async () => {
    renderModal()

    fireEvent.click(screen.getByText(/Implementación técnica/i))

    await waitFor(() => {
      expect(screen.getByRole('combobox')).toBeInTheDocument()
    })
    const select = screen.getByRole('combobox')
    const optionValues = Array.from(select.options).map(o => o.value)
    expect(optionValues).toEqual(['', 'kimi'])
  })

  it('no muestra select de motor para facetas no gobernadas (thot, seleccionado por default)', async () => {
    renderModal()

    // Selección inicial (hipatia/jekyll/thot) no incluye ninguna faceta
    // gobernada -- ningun <select> debe aparecer aunque el catalogo ya
    // haya cargado.
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/motors/capabilities', { credentials: 'include' }))

    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('deja motor sin setear (auto) cuando no se elige nada explicito en el select', async () => {
    let submitted = null
    renderModal({ onSubmit: (payload) => { submitted = payload; return Promise.resolve() } })

    fireEvent.click(screen.getByText(/Implementación técnica/i))
    await waitFor(() => expect(screen.getByRole('combobox')).toBeInTheDocument())

    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))

    await waitFor(() => expect(submitted).not.toBeNull())
    const kimiStep = submitted.steps.find(s => s.facet === 'kimi')
    expect(kimiStep).toBeDefined()
    expect(kimiStep.motor).toBeUndefined()
  })

  it('incluye el motor elegido explicitamente en el step al enviar', async () => {
    let submitted = null
    renderModal({ onSubmit: (payload) => { submitted = payload; return Promise.resolve() } })

    fireEvent.click(screen.getByText(/Implementación técnica/i))
    await waitFor(() => expect(screen.getByRole('combobox')).toBeInTheDocument())

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'kimi' } })
    fireEvent.click(screen.getByText(/Planificar y ejecutar/i))

    await waitFor(() => expect(submitted).not.toBeNull())
    const kimiStep = submitted.steps.find(s => s.facet === 'kimi')
    expect(kimiStep.motor).toBe('kimi')
  })
})
