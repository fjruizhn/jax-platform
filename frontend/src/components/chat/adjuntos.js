// Frente D (2026-09-17, RD4): /api/chat/upload devuelve un adjunto por
// referencia -- {id, tipo, nombre, bytes, mime?, origen?, caracteres?,
// recortado?, vista_previa?} -- nunca base64 ni el texto completo (contrato
// en docs/superpowers/specs/2026-09-17-frente-d-adjuntos-por-referencia.md).
// El compositor guarda además el File que el usuario eligió (`archivo`) y,
// para imagenes, un object URL local de vista previa (`previewUrl`); ninguno
// de los dos viaja al servidor. Los errores se traducen con el mecanismo
// compartido de la Mesa (erroresMesa / textoDeErrorDeMesa, api/errores.js).
export function cuerpoDeAdjunto(a) {
  return { id: a.id }
}

// Forma que Message.jsx ya dibuja (type/filename/base64). Para la imagen, el
// mensaje enviado se queda con SU PROPIO object URL -- distinto del que usa
// el compositor para la vista previa -- creado a partir del mismo File, así
// que removerlo/reemplazarlo en el compositor nunca rompe la miniatura ya
// mandada. Ese URL lo revoca el store al resetear la sesión (si hay hook),
// no el compositor.
export function vistaDeAdjunto(a) {
  return a.tipo === 'imagen'
    ? { type: 'image', filename: a.nombre, base64: URL.createObjectURL(a.archivo) }
    : { type: 'text', filename: a.nombre }
}

// Sin política cargada no se sabe: se asume que NO (fail-closed, igual que el servidor).
export function faltaSoporteDeImagen(adjunto, politica, facet) {
  return adjunto?.tipo === 'imagen' && !(politica?.facetas_con_imagen || []).includes(facet)
}
