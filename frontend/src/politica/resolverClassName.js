import * as TemaBotones from '../tema/botones.js'

// Resolución compartida de className de JSX a texto plano, para los
// detectores de tamaño de botón (botonesSinAreaDeToque.js,
// botonesConPocoRelleno.js). Antes cada detector reimplementaba esto por su
// cuenta; se consolida acá para no mantener dos copias con dos bugs
// distintos.
//
// PRINCIPIO CENTRAL (hallazgo de revisión, 2026-09-21): se resuelve por
// CONTENIDO, nunca por el NOMBRE de un identificador. La versión anterior
// tenía una lista a mano de "nombres de tokens conocidos que ya garantizan
// tamaño" (TOKENS_DE_TAMANO_CONOCIDOS) y la consultaba ANTES de mirar qué
// definía realmente ese nombre en el archivo -- reproducido:
//   const TAMANO_MINIMO_TOQUE = 'text-lg font-bold'
//   <button className={`${TAMANO_MINIMO_TOQUE} rounded`}>×</button>
// pasaba, 0 hallazgos, sin que el archivo importara tema/botones.js para
// nada. Ahora: un identificador sólo se trata como "ya trae tamaño" si el
// archivo lo importa DE VERDAD desde tema/botones.js -- se lee el valor REAL
// exportado por ese módulo, no una lista de nombres
// a mano (que además se rompía cada vez que tema/botones.js sumaba un
// token nuevo). Cualquier otro identificador (una constante local, o
// importada de cualquier otro archivo) se resuelve por su CONTENIDO real si
// se puede, o se declara irresoluble si no.
//
// IRRESOLUBLE = null, y null SIEMPRE significa "no se marca" en los
// detectores que usan esto -- nunca "se marca porque no se pudo verificar".
// Antes había una asimetría real: un className que era sólo un Identifier
// suelto e irresoluble se dejaba pasar (null), pero un TemplateLiteral con
// UNA interpolación irresoluble trataba esa interpolación como texto vacío
// y evaluaba el resto -- lo que podía marcar en falso un className cuyo
// ÚNICO tamaño viniera de esa interpolación no resuelta (una constante
// importada de otro archivo, o un ternario). Ahora cualquier interpolación
// irresoluble vuelve irresoluble TODO el className.

const RUTA_TEMA_BOTONES = /\/tema\/botones(\.js)?$/

// Valores reales exportados por tema/botones.js -- se lee el módulo de
// verdad (import estático de arriba), no una lista de nombres a mano. Un
// token nuevo que se agregue ahí queda cubierto acá sin tocar este archivo.
const EXPORTS_DE_TEMA_BOTONES = Object.fromEntries(
  Object.entries(TemaBotones).filter(([, valor]) => typeof valor === 'string'),
)

// Nombre local -> nombre EXPORTADO real, para cada especificador de import
// cuya fuente apunta a tema/botones.js (con cualquier profundidad de `../`).
// `import { TAMANO_BOTON_ACCION as X } from '...'` también se resuelve bien
// (se guarda X -> 'TAMANO_BOTON_ACCION', el nombre importado, no el local).
function nombresImportadosDeTemaBotones(programa) {
  const mapa = new Map()
  for (const nodo of programa.body) {
    if (nodo.type !== 'ImportDeclaration') continue
    if (!RUTA_TEMA_BOTONES.test(nodo.source.value)) continue
    for (const spec of nodo.specifiers) {
      if (spec.type === 'ImportSpecifier') mapa.set(spec.local.name, spec.imported.name)
    }
  }
  return mapa
}

// Nombre -> nodo de inicialización, para cada `const NOMBRE = ...` de nivel
// superior del archivo (StringLiteral o TemplateLiteral). Sólo dentro del
// mismo archivo y sólo cadenas simples -- nada de llamadas, ternarios ni
// imports de otros archivos (esos quedan irresolubles, a propósito: seguir
// una referencia a OTRO archivo es análisis de flujo entre módulos, no de
// sintaxis de éste).
function mapaDeConstantesLocales(programa) {
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

// Construye el resolvedor para UN archivo ya parseado: junta lo importado
// de tema/botones.js (resuelto a su valor real) y las constantes locales.
export function resolvedorDeArchivo(programa) {
  const importadosDeTema = nombresImportadosDeTemaBotones(programa)
  const constantesLocales = mapaDeConstantesLocales(programa)

  // Resuelve un Identifier a texto, o null si no se puede. `vistos` corta
  // ciclos en cadenas de constantes locales (A = `${B}`, B = `${A}`).
  function resolverIdentifier(nombre, vistos) {
    const nombreReal = importadosDeTema.get(nombre)
    if (nombreReal !== undefined) {
      // Importado DE VERDAD desde tema/botones.js: se usa el valor real
      // exportado con ESE nombre (no el nombre local, por si hay alias).
      return EXPORTS_DE_TEMA_BOTONES[nombreReal] ?? null
    }
    if (vistos.has(nombre)) return null // ciclo -- no debería pasar, no se cuelga
    const definicion = constantesLocales.get(nombre)
    if (!definicion) return null // ni importado de tema/botones ni definido acá: irresoluble
    return resolverNodo(definicion, new Set(vistos).add(nombre))
  }

  // Resuelve un StringLiteral/TemplateLiteral a texto, o null si CUALQUIER
  // parte no se puede resolver (ver nota de arriba sobre por qué una
  // interpolación irresoluble invalida TODO el className, no sólo esa parte).
  function resolverNodo(nodo, vistos = new Set()) {
    if (nodo.type === 'StringLiteral') return nodo.value
    if (nodo.type !== 'TemplateLiteral') return null
    const partes = nodo.quasis.map((q) => q.value.raw)
    const interpoladas = []
    for (const expr of nodo.expressions) {
      if (expr.type !== 'Identifier') return null // ternario, llamada, etc.: irresoluble
      const texto = resolverIdentifier(expr.name, vistos)
      if (texto === null) return null
      interpoladas.push(texto)
    }
    return partes.reduce((acc, parte, i) => `${acc}${parte} ${interpoladas[i] ?? ''}`, '')
  }

  // Texto del atributo className de un JSXOpeningElement, o null si no se
  // puede resolver (incluye: no hay className, o hay un JSXSpreadAttribute
  // que podría traer uno -- `<button {...props}>` no se puede descartar
  // como "sin className" sin saber qué hay en `props`).
  return function textoDeClassName(atributos) {
    // Un spread podría traer className (o no) -- no se puede saber sin
    // ejecutar el código. Se trata como "encontrado pero irresoluble", NO
    // como "sin className": lo primero no se marca en ninguno de los dos
    // detectores que usan esto, lo segundo sí se marcaría en
    // botonesSinAreaDeToque.js (ver su hallazgos.push del caso !encontrado).
    if (atributos.some((a) => a.type === 'JSXSpreadAttribute')) return { encontrado: true, texto: null }
    // JSX aplica el ÚLTIMO className si hay más de uno repetido (raro, pero
    // válido); .find() se quedaba con el primero.
    const atributo = [...atributos].reverse().find((a) => a.type === 'JSXAttribute' && a.name?.name === 'className')
    if (!atributo) return { encontrado: false, texto: null }
    const valor = atributo.value
    if (!valor) return { encontrado: true, texto: null }
    if (valor.type === 'StringLiteral') return { encontrado: true, texto: valor.value }
    if (valor.type === 'JSXExpressionContainer') {
      const expr = valor.expression
      if (expr.type === 'StringLiteral') return { encontrado: true, texto: expr.value }
      if (expr.type === 'TemplateLiteral') return { encontrado: true, texto: resolverNodo(expr) }
      if (expr.type === 'Identifier') return { encontrado: true, texto: resolverIdentifier(expr.name, new Set()) }
    }
    return { encontrado: true, texto: null }
  }
}
