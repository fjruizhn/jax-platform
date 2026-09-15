import { renderHook, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { useCerrarConEscape } from './useCerrarConEscape'

// Fix round 2 (2026-09-15, Ruling U25): antes, `onCerrar` iba en las deps del
// efecto -- una función nueva en cada render (como pasa un inline
// `() => setX(null)`) sacaba y volvía a poner el listener en cada render. El
// callback vive ahora en un ref actualizado en cada render, y el listener de
// `keydown` se agrega UNA sola vez.
describe('useCerrarConEscape', () => {
  it('agrega el listener de keydown una sola vez, no se re-agrega al cambiar onCerrar, y Escape llama al más reciente', () => {
    const addSpy = vi.spyOn(document, 'addEventListener')
    const removeSpy = vi.spyOn(document, 'removeEventListener')
    const keydownAdds = () => addSpy.mock.calls.filter(([evento]) => evento === 'keydown')
    const keydownRemoves = () => removeSpy.mock.calls.filter(([evento]) => evento === 'keydown')

    const onCerrar1 = vi.fn()
    const { rerender, unmount } = renderHook(
      ({ onCerrar }) => useCerrarConEscape(onCerrar),
      { initialProps: { onCerrar: onCerrar1 } },
    )
    expect(keydownAdds()).toHaveLength(1)

    const onCerrar2 = vi.fn()
    rerender({ onCerrar: onCerrar2 })
    // Sigue habiendo un solo listener agregado: no se sacó ni se volvió a poner.
    expect(keydownAdds()).toHaveLength(1)
    expect(keydownRemoves()).toHaveLength(0)

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onCerrar1).not.toHaveBeenCalled()
    expect(onCerrar2).toHaveBeenCalledTimes(1)

    unmount()
    expect(keydownRemoves()).toHaveLength(1)

    addSpy.mockRestore()
    removeSpy.mockRestore()
  })

  // U34 (2026-09-15): `null` = diálogo no cerrable (el cambio obligatorio de
  // contraseña). Escape no hace nada y no revienta.
  it('con null, Escape no llama a nada ni tira error', () => {
    const errores = []
    const alError = (e) => errores.push(e.error)
    window.addEventListener('error', alError)
    renderHook(() => useCerrarConEscape(null))
    fireEvent.keyDown(document, { key: 'Escape' })
    window.removeEventListener('error', alError)
    expect(errores).toHaveLength(0)
  })
})
