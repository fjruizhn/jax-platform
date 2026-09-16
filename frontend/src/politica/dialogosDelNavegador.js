import { parse } from '@babel/parser'

// Detector de diálogos del navegador (decisión de Fernando, 2026-09-15).
// Ninguna confirmación, aviso ni pedido de dato puede salir en una caja del
// sistema operativo: todo va en `components/Dialogo.jsx`, y en
// `ConfirmacionSuma` si la acción es destructiva.
//
// Se parsea de verdad en vez de mirar el texto crudo: sobre el texto, tanto
// `adminKeyModelDeleteConfirmSum` como un comentario que dice "reemplaza a
// window.confirm" dan falso positivo, y en cambio un `confirm(...)` pelado (sin
// el prefijo `window.`) pasaba desapercibido, que es justo el agujero que este
// módulo cierra.
export const OBJETOS_VENTANA = ['window', 'globalThis', 'self', 'top', 'parent']
export const DIALOGOS = ['confirm', 'alert', 'prompt']

const EN_SU_LUGAR = 'usá Dialogo (o ConfirmacionSuma si es destructivo), con el texto en i18n'

// Nombre de la propiedad accedida, tanto `window.confirm` como
// `window["confirm"]`. Un acceso computado con algo que no es un string
// literal (`window[x]`) no se puede resolver acá y no se marca.
function nombreDePropiedad(nodo) {
  if (!nodo.computed && nodo.property?.type === 'Identifier') return nodo.property.name
  if (nodo.computed && nodo.property?.type === 'StringLiteral') return nodo.property.value
  return null
}

// Texto de la llamada si el nodo es una llamada a un diálogo del navegador.
// No alcanza con que el nombre aparezca: tiene que ser el CALLEE de una
// llamada, pelado o colgando de un alias de la ventana. `acciones.confirm()`,
// `t.confirmSumLabel(n)` y `{ confirm: ... }` no son eso.
// Límite conocido (revisión 2026-09-15): sólo se resuelve el callee escrito a
// la vista -- un Identifier o un MemberExpression sobre la ventana. Si alguien
// guarda la función en una variable (`const c = window.confirm; c('x')`), el
// escaneo no la ve: seguirle el rastro a un valor a través de variables es
// análisis de flujo, no de sintaxis. Es angosto y deliberado; la regla la
// sostiene además la revisión de código.
function llamadaDeDialogo(nodo) {
  const callee = nodo.callee
  if (callee?.type === 'Identifier' && DIALOGOS.includes(callee.name)) return `${callee.name}(…)`
  if (callee?.type === 'MemberExpression' && callee.object?.type === 'Identifier'
      && OBJETOS_VENTANA.includes(callee.object.name)) {
    const propiedad = nombreDePropiedad(callee)
    if (propiedad && DIALOGOS.includes(propiedad)) return `${callee.object.name}.${propiedad}(…)`
  }
  return null
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
    // `loc`, `comments`, `leadingComments` y compañía no son código.
    if (clave === 'loc' || clave === 'range' || clave.endsWith('Comments') || clave === 'comments') continue
    if (valor && typeof valor === 'object') recorrer(valor, visitar)
  }
}

// Hallazgos de un archivo: cada uno con ruta, línea y qué usar en su lugar.
// Un archivo que no se puede parsear es una violación, no un salto: si no se
// puede leer el código, no se puede declarar limpio (Principio I).
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
    return [`${ruta}: no se pudo analizar (${e.message}); un archivo que no se parsea no se puede declarar sin diálogos del navegador`]
  }
  const hallazgos = []
  recorrer(arbol.program, (nodo) => {
    if (nodo.type !== 'CallExpression' && nodo.type !== 'OptionalCallExpression') return
    const llamada = llamadaDeDialogo(nodo)
    if (!llamada) return
    hallazgos.push(`${ruta}:${nodo.loc?.start.line ?? '?'}: llamada a ${llamada}, que abre una caja del navegador; ${EN_SU_LUGAR}`)
  })
  return hallazgos
}
