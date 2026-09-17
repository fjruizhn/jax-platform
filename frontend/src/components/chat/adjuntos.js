// Frente D (2026-09-16): lo que /api/chat/upload devuelve y lo que /api/chat
// acepta NO son la misma forma. El servidor tiene extra='forbid': solo viaja
// lo que el contrato declara (ni `bytes` ni `recortado`). Los errores se
// traducen con el mecanismo compartido de la Mesa (erroresMesa /
// textoDeErrorDeMesa, api/errores.js) -- no hay traductor propio de adjuntos.
export function cuerpoDeAdjunto(a) {
  if (a.tipo === 'imagen') {
    return { tipo: 'imagen', nombre: a.nombre, mime: a.mime, base64: a.base64 }
  }
  return { tipo: 'texto', origen: a.origen, nombre: a.nombre, contenido: a.contenido }
}

// Forma que Message.jsx ya dibuja (type/filename/base64 como data URI).
export function vistaDeAdjunto(a) {
  return a.tipo === 'imagen'
    ? { type: 'image', filename: a.nombre, base64: `data:${a.mime};base64,${a.base64}` }
    : { type: 'text', filename: a.nombre }
}

// Sin política cargada no se sabe: se asume que NO (fail-closed, igual que el servidor).
export function faltaSoporteDeImagen(adjunto, politica, facet) {
  return adjunto?.tipo === 'imagen' && !(politica?.facetas_con_imagen || []).includes(facet)
}
