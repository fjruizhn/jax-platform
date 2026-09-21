import { parse } from '@babel/parser'

// Detector de botones de ícono o de un solo glifo sin área de toque mínima
// (WCAG 2.2 §2.5.8 Target Size · Minimum: 24×24px). Hallazgo de revisión,
// 2026-09-21: cuatro botones de cerrar con un único glifo ("×"/"✕") y el
// ojito de PasswordInput (un <svg>) no tenían NINGUNA clase de tamaño -- el
// detector de tamanoDeToque.test.js no los veía porque nunca tuvieron el
// patrón de tamaño de tema/botones.js: esta es una familia distinta del
// mismo defecto (WCAG 2.2 2.5.8).
//
// Igual que dialogosDelNavegador.js: se parsea de verdad (@babel/parser) en
// vez de mirar el texto crudo, porque un botón con un ícono Y texto visible
// (p. ej. el selector de idioma de BarraUsuario, `<IconoGlobo/> <span>ES</span>`)
// NO es de esta familia -- ahí el ícono no es el único contenido.
//
// LO QUE CUBRE, a propósito y nada más (ver "Límite conocido" abajo): un
// `<button>` cuyo ÚNICO hijo con contenido es (a) un solo carácter de texto
// literal ("×", "✕", ...) o (b) un ícono -- un `<svg>` directo, un componente
// `Icono*`, o un ternario entre dos de esos (el ojito de PasswordInput). Si
// ese botón no tiene NINGUNA clase de tamaño (ni padding ni ancho/alto) en su
// `className`, se marca.
export const PREFIJOS_DE_TAMANO = /^(p|px|py|pt|pb|pl|pr|w|h|min-w|min-h)-/
// Constantes de esta casa que YA garantizan el mínimo (tema/botones.js):
// una referencia a una de estas en el className cuenta como tamaño resuelto,
// aunque el resto del className sea un literal sin marcas de tamaño.
export const TOKENS_DE_TAMANO_CONOCIDOS = ['TAMANO_MINIMO_TOQUE', 'TAMANO_BOTON_ACCION']

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

// El único hijo es un glifo suelto (un carácter de texto literal) o un
// ícono (un <svg>/Icono* directo, o el ternario entre dos de esos -- el
// patrón exacto del ojito de PasswordInput: `{visible ? <svg.../> : <svg.../>}`).
function esGlifoOIconoSuelto(hijos) {
  if (hijos.length !== 1) return false
  const [unico] = hijos
  if (unico.type === 'JSXText') {
    // Array.from cuenta puntos de código Unicode, no unidades UTF-16 --
    // un glifo como "×" es un solo carácter aunque algún emoji no lo sea.
    return Array.from(unico.value.trim()).length === 1
  }
  if (unico.type === 'JSXElement') return esNombreDeIcono(unico)
  if (unico.type === 'JSXExpressionContainer') {
    const expr = unico.expression
    if (expr.type === 'JSXElement') return esNombreDeIcono(expr)
    if (expr.type === 'ConditionalExpression') {
      return [expr.consequent, expr.alternate].every((rama) => rama.type === 'JSXElement' && esNombreDeIcono(rama))
    }
  }
  return false
}

// Mapa nombre -> nodo de inicialización de cada `const NOMBRE = ...` de
// nivel superior del archivo (StringLiteral o TemplateLiteral). Resuelve
// SÓLO dentro del mismo archivo y SÓLO cadenas simples -- nada de llamadas,
// ternarios ni imports: es la misma frontera angosta y deliberada que
// dialogosDelNavegador.js documenta para su propio análisis de callee.
// Sin esto, `BOTON_NEUTRO` (BarraUsuario.jsx) o `BOTON_SECUNDARIO`
// (AdminFacetBindings.jsx) -- constantes que sí traen tamaño, sólo que un
// paso más lejos -- darían falso positivo, exactamente lo que el hallazgo
// de revisión 2026-09-21 encontró de verdad (`IconoSalir` con `${BOTON} ...`,
// pillado por el escaneo de todo src al construir este mismo detector).
function mapaDeConstantes(programa) {
  const mapa = new Map()
  for (const nodo of programa.body) {
    if (nodo.type !== 'VariableDeclaration') continue
    for (const decl of nodo.declarations) {
      if (decl.id?.type === 'Identifier' && decl.init
          && (decl.init.type === 'StringLiteral' || decl.init.type === 'TemplateLiteral')) {
        mapa.set(decl.id.name, decl.init)
      }
    }
  }
  return mapa
}

// Texto resuelto de un nodo StringLiteral/TemplateLiteral, siguiendo
// referencias a otras constantes del mismo mapa (cadena, con guarda contra
// ciclos). Una interpolación que no es ni una constante conocida de tamaño
// ni otra constante resoluble del archivo no aporta texto (se ignora, no
// se inventa contenido).
function resolverTexto(nodo, mapa, vistos = new Set()) {
  if (nodo.type === 'StringLiteral') return nodo.value
  if (nodo.type !== 'TemplateLiteral') return ''
  const partes = nodo.quasis.map((q) => q.value.raw)
  const interpoladas = nodo.expressions.map((expr) => {
    if (expr.type !== 'Identifier') return ''
    if (TOKENS_DE_TAMANO_CONOCIDOS.includes(expr.name)) return 'min-h-6'
    if (vistos.has(expr.name)) return '' // ciclo -- no debería pasar, pero no se cuelga
    const definicion = mapa.get(expr.name)
    if (!definicion) return ''
    return resolverTexto(definicion, mapa, new Set(vistos).add(expr.name))
  })
  // Intercalar quasis e interpolaciones resueltas, con espacio de separador
  // (un TemplateLiteral tiene un quasi de más que expressions).
  return partes.reduce((acc, parte, i) => `${acc}${parte} ${interpoladas[i] ?? ''}`, '')
}

// Texto crudo del className a inspeccionar: de un StringLiteral directo, de
// un Identifier que resuelve a una constante del mismo archivo (ver
// mapaDeConstantes), o de un TemplateLiteral (con sus interpolaciones
// resueltas igual). Un className que no se puede resolver de ninguna de
// estas formas (una constante importada, una expresión, una llamada) se
// deja pasar sin marcar: ver "Límite conocido".
function textoDeClassName(valorAtributo, mapaConstantes) {
  if (!valorAtributo) return null
  if (valorAtributo.type === 'StringLiteral') return valorAtributo.value
  if (valorAtributo.type === 'JSXExpressionContainer') {
    const expr = valorAtributo.expression
    if (expr.type === 'StringLiteral' || expr.type === 'TemplateLiteral') {
      return resolverTexto(expr, mapaConstantes)
    }
    if (expr.type === 'Identifier') {
      if (TOKENS_DE_TAMANO_CONOCIDOS.includes(expr.name)) return 'min-h-6'
      const definicion = mapaConstantes.get(expr.name)
      return definicion ? resolverTexto(definicion, mapaConstantes, new Set([expr.name])) : null
    }
  }
  return null
}

// ¿El className trae AL MENOS una clase de tamaño (padding o ancho/alto)?
// Precisión deliberada: se tokeniza por espacio y se exige que el PREFIJO
// completo del token (tras sacar variantes como `hover:`/`disabled:`)
// empiece con p-/px-/.../w-/h-/min-w-/min-h- -- una búsqueda de subcadena
// cruda marcaría falso positivo con clases como `top-1/2` (contiene "p-")
// que no son de tamaño.
function tieneAlgunaClaseDeTamano(texto) {
  if (!texto) return false
  return texto.split(/\s+/).some((token) => {
    const base = token.includes(':') ? token.slice(token.lastIndexOf(':') + 1) : token
    return PREFIJOS_DE_TAMANO.test(base)
  })
}

// Hallazgos de un archivo: cada uno con ruta y línea.
// Límite conocido (documentado a propósito, no un descuido): este detector
// SÓLO cubre la familia ícono/glifo-suelto de arriba. NO cubre:
//   - Un botón con etiqueta de texto corta pero real (p. ej. el selector de
//     idioma "ES"/"EN": el texto sale de una variable en runtime, `{l}`, y
//     su longitud no se puede saber leyendo el código fuente sin evaluarlo).
//   - Un botón cuyo className es una constante IMPORTADA de otro archivo
//     (seguir esa referencia es análisis de flujo entre archivos, no de
//     sintaxis de este archivo). Las constantes definidas en el MISMO
//     archivo sí se resuelven (mapaDeConstantes/resolverTexto arriba).
//   - Si el padding presente ALCANZA el mínimo: sólo se verifica que exista
//     ALGUNA clase de tamaño, no que sume 24px -- calcular el tamaño real de
//     una combinación arbitraria de utilidades de Tailwind (padding + line-
//     height del texto, o el tamaño intrínseco de un ícono) es aritmética de
//     caja CSS completa, no algo que un análisis de sintaxis pueda resolver
//     con precisión razonable sin falsos positivos sobre patrones ya
//     compatibles hoy (p. ej. los íconos de BarraUsuario, con `p-1.5` +
//     ícono `w-4 h-4` = 28px, o AttachButton con `w-8 h-8` explícito).
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
  const mapaConstantes = mapaDeConstantes(arbol.program)
  const hallazgos = []
  recorrer(arbol.program, (nodo) => {
    if (nodo.type !== 'JSXElement') return
    const nombre = nodo.openingElement.name
    if (nombre.type !== 'JSXIdentifier' || nombre.name !== 'button') return
    const hijos = hijosConContenido(nodo.children)
    if (!esGlifoOIconoSuelto(hijos)) return
    const atributoClassName = nodo.openingElement.attributes.find(
      (a) => a.type === 'JSXAttribute' && a.name?.name === 'className',
    )
    if (!atributoClassName) {
      hallazgos.push(`${ruta}:${nodo.loc?.start.line ?? '?'}: botón de ícono/glifo suelto sin className; ${EN_SU_LUGAR}`)
      return
    }
    const texto = textoDeClassName(atributoClassName.value, mapaConstantes)
    // className={ALGUNA_CONSTANTE} sin poder resolverla: no se marca (ver
    // "Límite conocido").
    if (texto === null) return
    if (!tieneAlgunaClaseDeTamano(texto)) {
      hallazgos.push(`${ruta}:${nodo.loc?.start.line ?? '?'}: botón de ícono/glifo suelto sin ninguna clase de tamaño; ${EN_SU_LUGAR}`)
    }
  })
  return hallazgos
}
