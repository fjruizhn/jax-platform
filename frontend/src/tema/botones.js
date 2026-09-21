// Tamaño mínimo de un botón de acción secundario (WCAG 2.2 §2.5.8 Target
// Size · Minimum: 24×24px). El patrón que se usaba antes en cada pantalla,
// `text-xs px-2 py-1`, mide solo ~20px de alto -- bajo el mínimo. Medido
// 2026-09-21: 16 usos sueltos en 6 archivos (Memoria y los admin de modelos).
//
// Se centraliza acá en vez de tocar cada archivo por separado (Principio IV:
// lo que se repite, no se hardcodea). `min-h-6` = 24px; `inline-flex
// items-center justify-center` centra el texto dentro de esa altura mínima
// en vez de dejarlo pegado arriba cuando el padding solo no alcanza.
//
// El color/estado de cada botón (bg-*, hover:*, disabled:*) sigue viviendo
// en el llamador -- acá solo el tamaño, que es lo que WCAG exige y lo único
// que de verdad se repetía igual en los seis archivos.
//
// Guardado por politica/tamanoDeToque.test.js: el patrón `text-xs px-2 py-1`
// solo puede vivir acá.
export const TAMANO_BOTON_ACCION = 'text-xs px-2 py-1 min-h-6 inline-flex items-center justify-center'
