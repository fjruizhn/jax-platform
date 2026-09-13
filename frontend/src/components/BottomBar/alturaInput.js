// Alto de la caja de mensaje (2026-09-12, pedido de Fernando): crece con el
// texto hasta MAX_LINEAS_INPUT y recién ahí hace scroll. Antes quedaba en UNA
// línea (rows=1, sin ajuste de alto) y un prompt largo no se podía leer.
//
// Función pura: jsdom no calcula layout, así que la cuenta se prueba acá y el
// componente solo le pasa las medidas reales del textarea. Con box-sizing
// border-box (preflight de Tailwind), `scrollHeight` incluye el padding pero no
// el borde: el alto final es contenido + borde.

// Fernando pidió "al menos 5 líneas"; 8 deja margen para prompts largos.
export const MAX_LINEAS_INPUT = 8

export function alturaInput({ scrollHeight, lineHeight, paddingY, bordeY, maxLineas = MAX_LINEAS_INPUT }) {
  const maxContenido = lineHeight * maxLineas + paddingY
  return {
    altoPx: Math.min(scrollHeight, maxContenido) + bordeY,
    conScroll: scrollHeight > maxContenido,
  }
}
