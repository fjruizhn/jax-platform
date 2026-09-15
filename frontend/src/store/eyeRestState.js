// Estado visual de reposo del ojo HAL (Ruling 21, fix-vivo-brief.md §C):
// faceta jax_local sin actividad -- su token de identidad, pulso lento. Único lugar donde vive
// el valor: getEyeState (useJaxStore.js) lo usa para el estado normal del
// panel en reposo, y HalEye en modo `reposo` (Login, sin sesión) lo usa
// directo, sin pasar por la store, para no escribirlo a mano en dos sitios.
//
// Archivo aparte de useJaxStore.js a propósito: Login.test.jsx mockea el
// módulo entero de la store (vi.mock('../store/useJaxStore', ...)), y el
// modo `reposo` de HalEye tiene que seguir dando el color y la animación
// correctos ahí también -- no puede depender de nada que ese mock reemplace.
//
// Task 20 (spec 2026-09-14-tema-tokens §7.3, cierra el Ruling 22): guarda el
// NOMBRE del token (= FACET_TOKENS.jax_local en useJaxStore.js), no un hex;
// HalEye lo pinta con colorToken().
export const EYE_ESTADO_REPOSO = { token: 'faceta-jax-local', animation: 'pulse-slow' }
