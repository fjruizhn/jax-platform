// Tamaño mínimo de un botón de acción secundario (WCAG 2.2 §2.5.8 Target
// Size · Minimum: 24×24px). El patrón que se usaba antes en cada pantalla
// (16 usos sueltos en 6 archivos, Memoria y los admin de modelos) es el que
// sigue como base de esta constante -- `padding + min-h-6/min-w-6` en vez de
// sólo padding.
//
// CORRECCIÓN (medido con un Chrome real, headless, vía CDP -- Principio I --
// después de terminado el arreglo, 2026-09-21): el encargo original decía
// que ese patrón medía "~20px de alto, bajo el mínimo". Medido de verdad
// (getBoundingClientRect + getComputedStyle sobre el CSS compilado real de
// esta app), el patrón viejo medía exactamente **24.00px** -- EN el mínimo,
// no por debajo. La cifra "~20px" era una estimación a ojo, nunca antes
// verificada, y quedó repetida sin comprobar en la primera versión de este
// comentario y en el commit que lo introdujo. Se corrige acá, no se borra en
// silencio (protocolo de la Biblioteca: una memoria falsa se marca corregida).
//
// Esto NO deshace el arreglo: 24.00px exacto es un límite sin margen (medio
// pixel de redondeo, un cambio de fuente, un ajuste futuro de padding, y cae
// por debajo sin que nadie lo note) y el problema de ANCHO de abajo (`min-w-6`)
// es real y verificado, independiente de este punto. `min-h-6` sigue siendo
// la mejora correcta -- una garantía explícita en vez de un valor que da
// justo en el borde por casualidad.
//
// Se centraliza acá en vez de tocar cada archivo por separado (Principio IV:
// lo que se repite, no se hardcodea). `min-h-6` = 24px; `inline-flex
// items-center justify-center` centra el texto dentro de esa altura mínima
// en vez de dejarlo pegado arriba cuando el padding solo no alcanza.
//
// `min-w-6` (hallazgo de revisión, 2026-09-21) por el mismo motivo que el
// alto, y no es cosmético: sin él el ancho depende SOLO de `px-2` + lo que
// mida el texto del botón, y ese texto sale de i18n. Un rótulo corto en otro
// idioma (o uno que se agregue después, más compacto) puede angostar el
// botón por debajo de 24px sin que nadie toque una línea de CSS y sin que el
// detector lo vea -- el patrón `text-xs px-2 py-1` seguiría ahí, intacto.
// El cumplimiento de 2.5.8 (24×24, las DOS dimensiones) no puede depender de
// la longitud de una traducción.
//
// El color/estado de cada botón (bg-*, hover:*, disabled:*) sigue viviendo
// en el llamador -- acá solo el tamaño, que es lo que WCAG exige y lo único
// que de verdad se repetía igual en los seis archivos.
//
// SEGUNDA RONDA (hallazgo de revisión, 2026-09-21, misma auditoría que
// encontró el ancho de arriba): once usos MÁS de esta misma constante, en
// otra variante del patrón -- `text-xs ... py-0.5` (AdminUsers.jsx x8 vía
// ACCION_NEUTRA + 2 inline, AdminRepository.jsx x3 Preview/Download/Delete,
// PanelEjecutor.jsx x1, BottomBar.jsx x1 -- el selector de faceta). Ese
// `py-0.5` sí medía 20.00px de alto (medido igual que arriba), genuinamente
// bajo el mínimo -- a diferencia de `py-1`, no es un caso límite. No estaba
// en el recuento original de 16 porque es un literal distinto
// (`text-xs px-2 py-0.5`, no `text-xs px-2 py-1`) que tamanoDeToque.test.js
// no busca.
//
// `flex-shrink-0` (hallazgo de revisión, 2026-09-21): en un flex item, el
// `min-width` por DEFECTO del navegador es `auto` (el tamaño del contenido),
// lo que en la práctica IMPIDE encoger por debajo del texto -- fijar
// `min-w-6` a un valor más chico que eso hace lo contrario de lo que parece:
// HABILITA que el botón encoja hasta 24px bajo presión de un contenedor
// `flex-wrap` angosto, y como no hay `white-space: nowrap`, el texto se
// envuelve en vez de desbordar (medido con Chrome real vía CDP: a un ancho
// de contenedor razonable -- 350px, seis botones, un rótulo largo -- CON o
// SIN min-w-6 dan el MISMO resultado, el navegador ya envolvía el botón a su
// propia línea antes de necesitar encoger; recién a un ancho absurdo, 15px,
// aparece la diferencia real). Dos consumidores reales usan esta constante
// dentro de un `flex-wrap` con texto de largo variable (BottomBar.jsx, el
// selector de faceta con display_name configurable por admin; FichaDeHecho.jsx,
// los botones de acción con texto de i18n) -- `flex-shrink-0` cierra el
// riesgo en los dos de una vez, sin tocar cada consumidor por separado: el
// botón nunca encoge por debajo de su contenido, con o sin espacio de sobra.
//
// Guardado por politica/tamanoDeToque.test.js: el patrón `text-xs px-2 py-1`
// solo puede vivir acá.
export const TAMANO_BOTON_ACCION = 'text-xs px-2 py-1 min-h-6 min-w-6 flex-shrink-0 inline-flex items-center justify-center'

// Piso de 24×24 desnudo, sin opinar de fuente/ícono ni de color -- eso varía
// entre llamadores (text-lg, text-sm, text-xs; un <svg>, un glifo de texto o
// dos letras). Para cualquier botón que hoy NO tiene ninguna clase de tamaño
// propia (ni padding ni ancho/alto): un ícono, un glifo suelto ("×"/"✕") o
// una etiqueta de un par de caracteres.
//
// Hallazgo de revisión, 2026-09-21: cuatro botones de cerrar con un único
// glifo ("×"/"✕"), el ojito de PasswordInput (un <svg>) y el selector de
// idioma de Login/ResetPassword ("ES"/"EN") no tenían NINGUNA clase de
// tamaño -- así que el área de toque real era la del glifo, el ícono o el
// texto solos, muy por debajo del mínimo (el selector de idioma, con
// `py-0.5`, medía 20px de alto). El patrón `text-xs px-2 py-1` de arriba no
// los cazaba porque nunca lo tuvieron: es una familia distinta del mismo
// defecto.
//
// Guardado por politica/botonesSinAreaDeToque.test.js para la familia de
// ícono/glifo suelto -- ver ahí el alcance exacto de lo que ese detector
// puede y no puede cubrir (no cubre el selector de idioma: su tamaño depende
// de un array en runtime, no de texto literal en el código fuente).
export const TAMANO_MINIMO_TOQUE = 'min-h-6 min-w-6 flex-shrink-0 inline-flex items-center justify-center'
