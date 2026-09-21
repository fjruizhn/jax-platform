import { parse } from '@babel/parser'
import { resolvedorDeArchivo } from './resolverClassName.js'

// Detector de botones cuyo tamaño de texto + relleno vertical suma MENOS de
// 24px de alto (WCAG 2.2 §2.5.8 Target Size · Minimum). Hallazgo de
// revisión, 2026-09-21: el guardián de tamanoDeToque.test.js sólo prohíbe
// el literal exacto del patrón de tema/botones.js -- que, medido de verdad (ver
// tema/botones.js), da EXACTAMENTE 24px, en el mínimo, no una violación.
// La familia que sí viola es `text-xs ... py-0.5` (20px medido), y no tenía
// ningún guardián: 11 usos reales en AdminUsers.jsx, AdminRepository.jsx,
// PanelEjecutor.jsx y BottomBar.jsx quedaron así hasta esta ronda.
//
// A diferencia de tamanoDeToque.test.js (subcadena cruda, sensible al orden
// de las clases -- `text-xs py-1 px-2` lo esquiva), esto TOKENIZA el
// className y CALCULA el alto real a partir de la escala de Tailwind:
// alto = line-height del tamaño de texto + relleno vertical total. El orden
// de las clases en el string no importa.
//
// Valores de línea por defecto de Tailwind (NO personalizados en este
// proyecto -- verificado: tailwind.config.js no declara `fontSize`, ver
// resolverClassName.js/botonesSinAreaDeToque.js para el mismo criterio de
// "no inventar, verificar contra el config real").
const LINE_HEIGHT_PX = { 'text-xs': 16, 'text-sm': 20, 'text-base': 24, 'text-lg': 28, 'text-xl': 28 }

// Escala de espaciado de Tailwind: el valor N de una utilidad (`py-N`,
// `h-N`, ...) es N × 0.25rem = N × 4px, para N numérico simple (enteros o
// con un decimal, `1.5`, `2.5`, ...). Valores no numéricos (fracciones como
// `1/2`, arbitrarios `[10px]`) no se interpretan -- ver "Límite conocido".
function pxDeEscala(valorCrudo) {
  const n = Number(valorCrudo)
  return Number.isFinite(n) ? n * 4 : null
}

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

// Tokens SIN variante (`sm:`, `hover:`, ...) -- igual que
// botonesSinAreaDeToque.js, una clase condicional no garantiza el tamaño
// SIEMPRE, así que no participa del cálculo.
function tokensIncondicionales(texto) {
  return texto.split(/\s+/).filter((t) => t && !t.includes(':'))
}

// Alto calculado de un botón a partir de sus tokens, o null si no se puede
// calcular con confianza (ver "Límite conocido" de hallazgosEnFuente).
function altoCalculado(tokens) {
  // 1. ¿Hay una utilidad de alto explícito (h-N o min-h-N) que YA garantiza
  //    el mínimo por sí sola? Si la hay y alcanza, no hace falta seguir.
  for (const token of tokens) {
    const m = /^(?:min-)?h-(.+)$/.exec(token)
    if (!m) continue
    const px = pxDeEscala(m[1])
    if (px !== null && px >= 24) return 24 // alcanza; el valor exacto no importa más
  }

  // 2. Tamaño de texto (line-height base). Ambiguo (ninguno, o más de uno
  //    con valores distintos) -> no se puede calcular.
  const tamañosDeTexto = tokens.filter((t) => t in LINE_HEIGHT_PX)
  const valoresDeAlto = new Set(tamañosDeTexto.map((t) => LINE_HEIGHT_PX[t]))
  if (valoresDeAlto.size !== 1) return null
  const lineHeight = [...valoresDeAlto][0]

  // 3. Relleno vertical. `p-N` (los cuatro lados) y `py-N`/`pt-N`+`pb-N`
  //    (verticales específicos) a la vez son ambiguos -- cuál gana depende
  //    del orden en la hoja de estilos COMPILADA, no del className: no se
  //    adivina, se declara irresoluble.
  const pTokens = tokens.filter((t) => /^p-(.+)$/.test(t))
  const pyTokens = tokens.filter((t) => /^py-(.+)$/.test(t))
  const ptTokens = tokens.filter((t) => /^pt-(.+)$/.test(t))
  const pbTokens = tokens.filter((t) => /^pb-(.+)$/.test(t))
  // Sin NINGUNA utilidad de padding vertical: no se calcula "0 de relleno".
  // Un botón sin `p-`/`py-`/`pt-`/`pb-` puede ser el padding por defecto del
  // navegador (que este análisis no ve), o un enlace de texto en línea sin
  // apariencia de botón -- WCAG 2.5.8 exime explícitamente el caso "el
  // objetivo está dentro de una oración o su tamaño lo determina el
  // line-height de texto no-objetivo" (la excepción "Inline"). Decidir si
  // un botón concreto cae en esa excepción es una lectura del diseño visual,
  // no algo que este análisis de sintaxis pueda resolver -- se declara
  // irresoluble, no se asume relleno cero. La familia que este detector
  // existe para cazar (AdminUsers.jsx, AdminRepository.jsx, ...) siempre
  // tenía un `py-0.5` ESCRITO -- nunca ausencia total de padding.
  if (pTokens.length === 0 && ptTokens.length === 0 && pbTokens.length === 0 && pyTokens.length === 0) return null
  const usaEspecificos = pyTokens.length > 0 || ptTokens.length > 0 || pbTokens.length > 0
  if (pTokens.length > 0 && usaEspecificos) return null // ambiguo, no se adivina
  if (pTokens.length > 1 || pyTokens.length > 1) return null // repetido, ambiguo

  let relleno = 0
  if (pTokens.length === 1) {
    const px = pxDeEscala(pTokens[0].slice(2))
    if (px === null) return null
    relleno = px * 2
  } else if (pyTokens.length === 1) {
    const px = pxDeEscala(pyTokens[0].slice(3))
    if (px === null) return null
    relleno = px * 2
  } else {
    // pt/pb sueltos (sin py): sumar lo que haya, 0 si falta uno de los dos
    // (el lado que falta no tiene relleno declarado -- no es ambiguo, es 0).
    for (const [lista, prefijoLen] of [[ptTokens, 3], [pbTokens, 3]]) {
      if (lista.length > 1) return null
      if (lista.length === 1) {
        const px = pxDeEscala(lista[0].slice(prefijoLen))
        if (px === null) return null
        relleno += px
      }
    }
  }

  return lineHeight + relleno
}

// Hallazgos de un archivo: cada uno con ruta y línea.
//
// LÍMITE CONOCIDO, declarado a propósito (misma disciplina que
// botonesSinAreaDeToque.js): esto NO cubre:
//   - Un botón sin ninguna clase `text-*` reconocida (el tamaño de fuente
//     sale del contexto heredado, que este análisis no puede seguir).
//   - `p-N` combinado con `py-N`/`pt-N`/`pb-N` a la vez: cuál gana depende
//     del orden en el CSS compilado por Tailwind, no del className -- se
//     declara irresoluble en vez de adivinar.
//   - Valores arbitrarios (`p-[10px]`) o fraccionarios (`p-1/2`): la escala
//     numérica simple de Tailwind es la única que se interpreta.
//   - Mismos límites de resolución de className que
//     botonesSinAreaDeToque.js (ver resolverClassName.js): constantes
//     importadas de archivos que no son tema/botones.js, spread props,
//     interpolaciones no resolubles.
//   - Sólo `<button>`. `<a>`, `<input>`, `<select>` y `[role="button"]`
//     quedan fuera.
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
    const { texto } = textoDeClassName(nodo.openingElement.attributes)
    if (texto === null) return // sin className, irresoluble, o sin texto -- no se marca
    const alto = altoCalculado(tokensIncondicionales(texto))
    if (alto !== null && alto < 24) {
      hallazgos.push(`${ruta}:${nodo.loc?.start.line ?? '?'}: botón de ${alto}px de alto calculado, bajo el mínimo de 24px (WCAG 2.2 2.5.8) -- dale TAMANO_BOTON_ACCION de tema/botones.js`)
    }
  })
  return hallazgos
}
