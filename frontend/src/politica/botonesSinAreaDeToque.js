import { parse } from '@babel/parser'
import { resolvedorDeArchivo } from './resolverClassName.js'

// Detector de botones de ícono o de un solo glifo sin área de toque mínima
// (WCAG 2.2 §2.5.8 Target Size · Minimum: 24×24px). Hallazgo de revisión,
// 2026-09-21: cuatro botones de cerrar con un único glifo ("×"/"✕") y el
// ojito de PasswordInput (un <svg>) no tenían NINGUNA clase de tamaño -- el
// detector de tamanoDeToque.test.js no los veía porque nunca tuvieron ese
// patrón: esta es una familia distinta del mismo defecto.
//
// Igual que dialogosDelNavegador.js: se parsea de verdad (@babel/parser) en
// vez de mirar el texto crudo, porque un botón con un ícono Y texto visible
// (p. ej. el selector de idioma de BarraUsuario, `<IconoGlobo/> <span>ES</span>`)
// NO es de esta familia -- ahí el ícono no es el único contenido.
//
// LO QUE CUBRE, a propósito: un `<button>` cuyo ÚNICO hijo con contenido es
// (a) un solo carácter de texto -- literal (`×`) o como expresión (`{'×'}`)
// -- o (b) un ícono -- un `<svg>` directo, un componente `Icono*`, o un
// ternario entre dos de esos (el ojito de PasswordInput). Si ese botón no
// tiene NINGUNA clase de tamaño (ni padding ni ancho/alto, aplicada SIEMPRE
// -- ver más abajo) en su `className`, se marca.
export const PREFIJOS_DE_TAMANO = /^(p|px|py|pt|pb|pl|pr|w|h|min-w|min-h)-(.+)$/

const EN_SU_LUGAR = `dale un tamaño mínimo -- min-h-6 min-w-6, o TAMANO_MINIMO_TOQUE de tema/botones.js`

function recorrer(nodo, visitar) {
  if (!nodo || typeof nodo !== 'object') return
  if (Array.isArray(nodo)) {
    for (const hijo of nodo) recorrer(hijo, visitar)
    return
  }
  if (typeof nodo.type !== 'string') return
  visitar(nodo)
  for (const [clave, valor] of Object.entries(nodo)) {
    if (clave === 'loc' || clave === 'range' || clave.endsWith('Comments') || clave === 'comments') continue
    if (valor && typeof valor === 'object') recorrer(valor, visitar)
  }
}

// Hijos "con contenido" de un elemento JSX: fuera el texto que es sólo
// espacio/salto de línea (indentación del formateo) y los comentarios
// `{/* ... */}` (JSXExpressionContainer sobre JSXEmptyExpression).
function hijosConContenido(children) {
  return children.filter((hijo) => {
    if (hijo.type === 'JSXText') return hijo.value.trim() !== ''
    if (hijo.type === 'JSXExpressionContainer') return hijo.expression.type !== 'JSXEmptyExpression'
    return true
  })
}

function esNombreDeIcono(nodoJSXElement) {
  const nombre = nodoJSXElement.openingElement?.name
  if (nombre?.type !== 'JSXIdentifier') return false
  return nombre.name === 'svg' || /^Icono/.test(nombre.name)
}

// Un carácter, contando puntos de código Unicode (no unidades UTF-16) --
// "×" es un carácter aunque algún emoji no lo sea.
function esUnSoloCaracter(texto) {
  return Array.from(texto.trim()).length === 1
}

// El único hijo es un glifo suelto (un carácter de texto, literal o como
// expresión de un StringLiteral -- `{'×'}`, forma que el texto literal NO
// cubría) o un ícono (un <svg>/Icono* directo, o el ternario entre dos de
// esos -- el patrón exacto del ojito de PasswordInput).
function esGlifoOIconoSuelto(hijos) {
  if (hijos.length !== 1) return false
  const [unico] = hijos
  if (unico.type === 'JSXText') return esUnSoloCaracter(unico.value)
  if (unico.type === 'JSXElement') return esNombreDeIcono(unico)
  if (unico.type === 'JSXExpressionContainer') {
    const expr = unico.expression
    if (expr.type === 'StringLiteral') return esUnSoloCaracter(expr.value)
    if (expr.type === 'JSXElement') return esNombreDeIcono(expr)
    if (expr.type === 'ConditionalExpression') {
      return [expr.consequent, expr.alternate].every((rama) => rama.type === 'JSXElement' && esNombreDeIcono(rama))
    }
  }
  return false
}

// ¿El className trae AL MENOS una clase de tamaño (padding o ancho/alto) que
// aplique SIEMPRE? Precisión deliberada:
//  - Se tokeniza por espacio y se exige que el PREFIJO completo del token
//    empiece con p-/px-/.../w-/h-/min-w-/min-h- -- una búsqueda de
//    subcadena cruda marcaría falso positivo con clases como `top-1/2`
//    (contiene "p-") que no son de tamaño.
//  - Una clase con VARIANTE (`sm:p-2`, `hover:p-2`, `disabled:p-2`) NO
//    cuenta: es condicional. `sm:p-2` no aplica bajo el breakpoint `sm`, así
//    que en un teléfono angosto el botón mide lo que mida sin ella -- el
//    hallazgo de revisión 2026-09-21 lo marcó explícitamente como hueco.
//    Sólo una clase SIN el prefijo de variante (aplica siempre) cuenta.
//  - Un valor de CERO (`p-0`, `w-0`, `min-h-0`) no aporta nada -- contarlo
//    como "tiene tamaño" es lo mismo que no chequear nada. Se excluye el
//    valor `0` explícitamente (mismo hallazgo).
function tieneAlgunaClaseDeTamano(texto) {
  if (!texto) return false
  return texto.split(/\s+/).some((token) => {
    if (token.includes(':')) return false // variante: condicional, no cuenta
    const match = PREFIJOS_DE_TAMANO.exec(token)
    if (!match) return false
    return match[2] !== '0' // p-0/w-0/min-h-0: valor nulo, no cuenta
  })
}

// Hallazgos de un archivo: cada uno con ruta y línea.
//
// LÍMITE CONOCIDO, declarado a propósito (hallazgo de revisión 2026-09-21:
// un detector con huecos no declarados da una sensación de cobertura que no
// existe, que es peor que no tenerlo). Esto NO cubre:
//   - Un botón con etiqueta de texto corta pero real de MÁS de un carácter
//     (p. ej. el selector de idioma "ES"/"EN": el texto sale de una
//     variable en runtime, `{l}`, y su longitud no se puede saber leyendo
//     el código fuente sin evaluarlo).
//   - Un ícono cuyo componente NO se llama `Icono*` ni es `<svg>` -- p. ej.
//     `<XIcon/>` de una librería con otra convención de nombre. Detectar
//     "esto es un ícono" sin ejecutar el código (saber qué renderiza el
//     componente) es indistinguible, con sintaxis sola, de cualquier otro
//     componente de una letra con contenido no-textual -- no hay un límite
//     razonable de nombres a mano que no quede desactualizado.
//   - Un ícono envuelto en un elemento contenedor (`<span><svg/></span>`):
//     el único hijo del botón pasa a ser el `<span>`, que no matchea ni
//     glifo ni ícono, y el chequeo no baja un nivel más. Bajar recursión
//     abre más casos (¿y si el span TIENE su propio padding que ya
//     resuelve el tamaño? ¿y si tiene más contenido al lado?) que dejan de
//     ser "barato" de resolver con precisión.
//   - Una CONSTANTE con tamaño real pero importada de un archivo que NO es
//     tema/botones.js (ver resolverClassName.js): no se sigue esa
//     referencia -- análisis de flujo entre módulos arbitrarios, no de
//     sintaxis de este archivo.
//   - Sólo se mira la etiqueta `<button>`. `<a>`, `<input>`, `<select>` y
//     cualquier `[role="button"]` sobre otro elemento quedan ENTERAMENTE
//     fuera del alcance de este detector.
// Un archivo que no se puede parsear es una violación, no un salto.
export function hallazgosEnFuente(codigo, ruta) {
  let arbol
  try {
    arbol = parse(codigo, {
      sourceType: 'module',
      plugins: ['jsx'],
      attachComment: false,
      errorRecovery: false,
    })
  } catch (e) {
    return [`${ruta}: no se pudo analizar (${e.message}); un archivo que no se parsea no se puede declarar sin esta violación`]
  }
  const textoDeClassName = resolvedorDeArchivo(arbol.program)
  const hallazgos = []
  recorrer(arbol.program, (nodo) => {
    if (nodo.type !== 'JSXElement') return
    const nombre = nodo.openingElement.name
    if (nombre.type !== 'JSXIdentifier' || nombre.name !== 'button') return
    const hijos = hijosConContenido(nodo.children)
    if (!esGlifoOIconoSuelto(hijos)) return
    const { encontrado, texto } = textoDeClassName(nodo.openingElement.attributes)
    if (!encontrado) {
      hallazgos.push(`${ruta}:${nodo.loc?.start.line ?? '?'}: botón de ícono/glifo suelto sin className; ${EN_SU_LUGAR}`)
      return
    }
    // texto === null: irresoluble (spread props, constante importada de
    // otro archivo, ternario interpolado, ...) -- no se marca (ver "Límite
    // conocido" de resolverClassName.js).
    if (texto === null) return
    if (!tieneAlgunaClaseDeTamano(texto)) {
      hallazgos.push(`${ruta}:${nodo.loc?.start.line ?? '?'}: botón de ícono/glifo suelto sin ninguna clase de tamaño; ${EN_SU_LUGAR}`)
    }
  })
  return hallazgos
}
