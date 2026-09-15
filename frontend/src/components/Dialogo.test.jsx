import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import '@testing-library/jest-dom'
import { useState } from 'react'
import Dialogo from './Dialogo'

// Ruling U27 (review final de la etapa 4, 2026-09-15): un único primitivo de
// diálogo. Cada modal hacía el diálogo a mano y a ninguno le salía completo:
// el fondo (sidebar incluido) seguía alcanzable, el foco no entraba ni volvía
// al disparador, y el alta no era siquiera un diálogo. Estos tests fijan el
// contrato: portal a document.body, `inert` en #root con contador, foco
// adentro al abrir y de vuelta al disparador al cerrar (con #root ya sin
// inert), Escape cierra, clic en el fondo no, y el cableado ARIA.

// El #root real de index.html: la app vive adentro y el diálogo afuera.
let root
beforeEach(() => {
  root = document.createElement('div')
  root.id = 'root'
  document.body.appendChild(root)
})
afterEach(() => {
  root.remove()
})

function Prueba({ onCerrar = vi.fn(), conCampo = true, inicial = false }) {
  const [abierto, setAbierto] = useState(inicial)
  return (
    <>
      <button onClick={() => setAbierto(true)}>abrir</button>
      {abierto && (
        <Dialogo idTitulo="dlg-titulo" titulo="Título de prueba" onCerrar={() => { onCerrar(); setAbierto(false) }}>
          {conCampo && <input aria-label="campo" />}
          <button onClick={() => setAbierto(false)}>cerrar</button>
        </Dialogo>
      )}
    </>
  )
}

function montar(props) {
  return render(<Prueba {...props} />, { container: root })
}

describe('Dialogo -- inert en #root (a)', () => {
  it('#root lleva inert mientras está abierto y no después de cerrarlo', () => {
    montar()
    expect(root).not.toHaveAttribute('inert')
    fireEvent.click(screen.getByText('abrir'))
    expect(root).toHaveAttribute('inert')
    fireEvent.click(screen.getByText('cerrar'))
    expect(root).not.toHaveAttribute('inert')
  })

  it('con dos diálogos abiertos, cerrar uno deja #root inert', () => {
    function Dos() {
      const [a, setA] = useState(true)
      const [b, setB] = useState(true)
      return (
        <>
          {a && <Dialogo idTitulo="t-a" titulo="A" onCerrar={() => setA(false)}><button onClick={() => setA(false)}>cerrar A</button></Dialogo>}
          {b && <Dialogo idTitulo="t-b" titulo="B" onCerrar={() => setB(false)}><button onClick={() => setB(false)}>cerrar B</button></Dialogo>}
        </>
      )
    }
    render(<Dos />, { container: root })
    expect(root).toHaveAttribute('inert')
    fireEvent.click(screen.getByText('cerrar A'))
    expect(root).toHaveAttribute('inert')
    fireEvent.click(screen.getByText('cerrar B'))
    expect(root).not.toHaveAttribute('inert')
  })

  it('el diálogo se monta fuera de #root (portal a body), así no queda inert él también', () => {
    montar({ inicial: true })
    const dialogo = screen.getByRole('dialog')
    expect(root.contains(dialogo)).toBe(false)
    expect(document.body.contains(dialogo)).toBe(true)
  })

  it('desmontar con el diálogo abierto también quita inert', () => {
    const { unmount } = montar({ inicial: true })
    expect(root).toHaveAttribute('inert')
    unmount()
    expect(root).not.toHaveAttribute('inert')
  })
})

describe('Dialogo -- foco (b, c)', () => {
  it('al abrir, el foco va al primer campo', () => {
    montar()
    fireEvent.click(screen.getByText('abrir'))
    expect(screen.getByLabelText('campo')).toHaveFocus()
  })

  it('sin campos, el foco va al título (tabIndex -1)', () => {
    montar({ conCampo: false })
    fireEvent.click(screen.getByText('abrir'))
    const titulo = screen.getByText('Título de prueba')
    expect(titulo).toHaveFocus()
    expect(titulo).toHaveAttribute('tabindex', '-1')
  })

  it('al cerrar, el foco vuelve al disparador, y #root ya no está inert en ese momento', () => {
    montar()
    const disparador = screen.getByText('abrir')
    disparador.focus()
    let inertAlRecibirFoco = null
    disparador.addEventListener('focus', () => { inertAlRecibirFoco = root.hasAttribute('inert') })
    fireEvent.click(disparador)
    expect(disparador).not.toHaveFocus()
    fireEvent.click(screen.getByText('cerrar'))
    expect(disparador).toHaveFocus()
    expect(inertAlRecibirFoco).toBe(false)
  })
})

describe('Dialogo -- cerrar (d)', () => {
  it('Escape llama a onCerrar', () => {
    const onCerrar = vi.fn()
    montar({ onCerrar, inicial: true })
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onCerrar).toHaveBeenCalledTimes(1)
  })

  it('un clic en el fondo NO cierra (no se pierde lo escrito)', () => {
    const onCerrar = vi.fn()
    montar({ onCerrar, inicial: true })
    const velo = screen.getByRole('dialog').parentElement
    fireEvent.mouseDown(velo)
    fireEvent.click(velo)
    expect(onCerrar).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})

describe('Dialogo -- ARIA (e)', () => {
  it('role dialog, aria-modal y aria-labelledby apuntando al título', () => {
    montar({ inicial: true })
    const dialogo = screen.getByRole('dialog', { name: 'Título de prueba' })
    expect(dialogo).toHaveAttribute('aria-modal', 'true')
    expect(dialogo).toHaveAttribute('aria-labelledby', 'dlg-titulo')
    expect(document.getElementById('dlg-titulo')).toHaveTextContent('Título de prueba')
  })

  it('el panel conserva sus clases de token y suma las de tamaño del llamador', () => {
    render(
      <Dialogo idTitulo="t" titulo="T" onCerrar={vi.fn()} className="max-w-lg"><p>x</p></Dialogo>,
      { container: root },
    )
    const panel = screen.getByRole('dialog')
    expect(panel.className).toMatch(/(^|\s)max-w-lg(\s|$)/)
    expect(panel.className).not.toMatch(/(^|\s)max-w-md(\s|$)/)
    expect(panel.className).toMatch(/bg-superficie border border-borde rounded-xl p-6 w-full/)
    expect(panel.parentElement.className).toMatch(/(^|\s)bg-fondo\/70(\s|$)/)
  })
})

// U34 (2026-09-15): el cambio obligatorio de contraseña no se puede cerrar.
describe('Dialogo -- cerrable={false}', () => {
  it('con cerrable={false}, Escape no cierra y el resto del contrato sigue', () => {
    const onCerrar = vi.fn()
    render(
      <Dialogo idTitulo="dlg-fijo" titulo="Obligatorio" onCerrar={onCerrar} cerrable={false}>
        <input aria-label="campo" />
      </Dialogo>
    )
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onCerrar).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: 'Obligatorio' })).toBeInTheDocument()
    expect(root).toHaveAttribute('inert')
    expect(screen.getByLabelText('campo')).toHaveFocus()
  })
})
