// Estado visual de reposo del ojo HAL (Ruling 21, fix-vivo-brief.md §C):
// faceta jax_local sin actividad -- azul, pulso lento. Único lugar donde vive
// el valor: getEyeState (useJaxStore.js) lo usa para el estado normal del
// panel en reposo, y HalEye en modo `reposo` (Login, sin sesión) lo usa
// directo, sin pasar por la store, para no escribirlo a mano en dos sitios.
//
// Archivo aparte de useJaxStore.js a propósito: Login.test.jsx mockea el
// módulo entero de la store (vi.mock('../store/useJaxStore', ...)), y el
// modo `reposo` de HalEye tiene que seguir dando el color y la animación
// correctos ahí también -- no puede depender de nada que ese mock reemplace.
export const COLOR_JAX_LOCAL = '#3b82f6' // = FACET_COLORS.jax_local en useJaxStore.js

export const EYE_ESTADO_REPOSO = { color: COLOR_JAX_LOCAL, animation: 'pulse-slow' }
