// Texto gobernado (F2-C/F2-D) en el chat.
//
// El renderer de jax proyecta el texto con html.escape(text, quote=True) --
// contrato F2-C, no se toca en jax. React ya escapa al pintar un nodo de
// texto, así que sin esta capa el usuario ve `&#x27;`, `&amp;`... literales.
// Acá se deshace SOLO esa proyección: las cinco entidades que produce
// html.escape y nada más (ni numéricas ni nombradas ajenas). El resultado va
// siempre como nodo de texto de React, nunca como HTML ni como Markdown.

const ENTIDADES = {
  '&amp;': '&',
  '&lt;': '<',
  '&gt;': '>',
  '&quot;': '"',
  '&#x27;': "'",
}

// Una sola pasada: `&amp;lt;` es el texto literal `&lt;`, no `<`.
export function decodificarTextoGobernado(texto) {
  if (typeof texto !== 'string') return ''
  return texto.replace(/&(?:amp|lt|gt|quot|#x27);/g, (e) => ENTIDADES[e])
}

// Códigos estables de aviso del servidor que el frontend muestra en su i18n.
export const AVISO_RESPUESTA_NO_VERIFICABLE = 'respuesta_no_verificable'

// El aviso degradado fijo del servidor se reconoce por el estado de contrato
// (DEGRADED_STRUCTURED, o UNAVAILABLE si el renderer falló), nunca por su
// texto: el texto del servidor puede cambiar sin tocar el contrato de bytes.
const ESTADOS_AVISO_DEGRADADO = new Set(['DEGRADED_STRUCTURED', 'UNAVAILABLE'])

export function avisoGobernadoDe(data) {
  if (data?.governed_plain === true && ESTADOS_AVISO_DEGRADADO.has(data.contract_state)) {
    return AVISO_RESPUESTA_NO_VERIFICABLE
  }
  return null
}
