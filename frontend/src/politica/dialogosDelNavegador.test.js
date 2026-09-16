// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { hallazgosEnFuente, OBJETOS_VENTANA, DIALOGOS } from './dialogosDelNavegador.js'

// Guarda: ningún diálogo del navegador en el frontend (decisión de Fernando,
// 2026-09-15). Ni confirmación, ni aviso, ni pedido de dato pueden salir en una
// caja del sistema operativo: todo va en `components/Dialogo.jsx` (y en
// `ConfirmacionSuma` si es destructivo), con nuestro texto en i18n, nuestro
// tema y el mismo comportamiento de foco y Escape que el resto de los modales.
//
// El control anterior sólo buscaba el texto "window.confirm". Un `confirm(...)`
// pelado abre la MISMA caja del navegador y el escaneo seguía verde: la misma
// forma de falla que el escaneo de `except` que era ciego en los worktrees.
//
// Por qué AST y no regex: una regex sobre el texto crudo no distingue una
// llamada de un identificador que contiene la palabra
// (`adminKeyModelDeleteConfirmSum`), de una clave de i18n, de un comentario o
// de este mismo archivo, que está lleno de código de mentira dentro de
// strings. Se parsea con @babel/parser (declarado en devDependencies; ya venía
// en el árbol como dependencia de @vitejs/plugin-react → @babel/core).
//
// El escaneo cubre TODO src -- código de producción y tests por igual --, con
// el mismo import.meta.glob que usa src/tema/contraste.test.js.
const fuentes = import.meta.glob('../**/*.{js,jsx}', {
  query: '?raw', import: 'default', eager: true,
})
const porRuta = Object.fromEntries(Object.entries(fuentes).map(([k, v]) => [k.replace(/^\.\.\//, ''), v]))
// import.meta.glob EXCLUYE al archivo que lo escribe (evita importarse a sí
// mismo), así que este mismo test quedaba fuera del escaneo -- un agujero del
// tamaño exacto del archivo que define la regla. Se lee por fs, igual que
// contraste.test.js lee el CSS que el bundler no le entrega.
const YO = 'politica/dialogosDelNavegador.test.js'
porRuta[YO] = readFileSync(new URL(import.meta.url), 'utf8')

describe('detector de diálogos del navegador', () => {
  it('marca una llamada pelada a confirm, alert o prompt, con archivo y línea', () => {
    const hallazgos = hallazgosEnFuente('const a = 1\nif (confirm("¿seguro?")) borrar()\n', 'x.jsx')
    expect(hallazgos).toHaveLength(1)
    expect(hallazgos[0]).toContain('x.jsx:2')
    expect(hallazgos[0]).toContain('confirm(')
  })

  it('marca window.alert y globalThis.prompt', () => {
    expect(hallazgosEnFuente('window.alert("hola")', 'a.js')).toEqual([
      expect.stringContaining('window.alert('),
    ])
    expect(hallazgosEnFuente('globalThis.prompt("nombre")', 'b.js')).toEqual([
      expect.stringContaining('globalThis.prompt('),
    ])
  })

  it('marca los demás alias de la ventana y la forma con corchetes', () => {
    for (const objeto of OBJETOS_VENTANA) {
      for (const dialogo of DIALOGOS) {
        expect(hallazgosEnFuente(`${objeto}.${dialogo}("x")`, 'c.js')).toHaveLength(1)
        expect(hallazgosEnFuente(`${objeto}["${dialogo}"]("x")`, 'c.js')).toHaveLength(1)
      }
    }
  })

  it('dice qué usar en su lugar', () => {
    const [hallazgo] = hallazgosEnFuente('window.confirm("x")', 'c.jsx')
    expect(hallazgo).toContain('Dialogo')
    expect(hallazgo).toContain('ConfirmacionSuma')
  })

  it('NO marca identificadores que sólo contienen la palabra', () => {
    const fuente = [
      "const [confirm, setConfirm] = useState('')",
      'const listo = confirming && confirmSumLabel',
      't.confirmSumLabel(n)',
      't.adminKeyModelDeleteConfirmSum(n)',
      'setConfirm("")',
      'const acciones = { confirm: () => {}, alert: () => {} }',
      'acciones.confirm()',
      'promptDelUsuario.prompt_text',
    ].join('\n')
    expect(hallazgosEnFuente(fuente, 'd.jsx')).toEqual([])
  })

  it('NO marca comentarios ni strings que mencionan un diálogo del navegador', () => {
    const fuente = [
      '// reemplaza a window.confirm: la baja pasa por ConfirmacionSuma',
      '/* nada de alert("x") ni prompt("y") */',
      'const texto = "window.confirm(1)"',
      "const otro = 'confirm(2)'",
      'const plantilla = `alert(3)`',
    ].join('\n')
    expect(hallazgosEnFuente(fuente, 'e.jsx')).toEqual([])
  })

  it('un archivo que no se puede parsear es una violación, no un salto', () => {
    const hallazgos = hallazgosEnFuente('const = {', 'roto.js')
    expect(hallazgos).toHaveLength(1)
    expect(hallazgos[0]).toContain('roto.js')
    expect(hallazgos[0]).toMatch(/no se pudo analizar/i)
  })

  it('parsea JSX sin ahogarse, y lo marca dentro del JSX también', () => {
    const fuente = '<button onClick={() => window.confirm("x")}>ok</button>'
    expect(hallazgosEnFuente(`export const B = () => (${fuente})`, 'f.jsx')).toHaveLength(1)
    expect(hallazgosEnFuente('export const B = () => (<b className="text-texto">ok</b>)', 'g.jsx')).toEqual([])
  })
})

describe('todo src sin diálogos del navegador', () => {
  it('ningún .js/.jsx de src llama a confirm, alert ni prompt', () => {
    expect(Object.keys(porRuta).length).toBeGreaterThan(40) // verde sobre cero archivos no vale
    const hallazgos = Object.entries(porRuta).flatMap(([ruta, codigo]) => hallazgosEnFuente(codigo, ruta))
    expect(hallazgos).toEqual([])
  })

  it('el escaneo incluye los tests, no sólo el código de producción', () => {
    expect(Object.keys(porRuta).some((r) => r.endsWith('.test.jsx'))).toBe(true)
    expect(Object.keys(porRuta)).toContain('politica/dialogosDelNavegador.test.js')
  })
})
