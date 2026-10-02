// Claves de localStorage que dejó funcionalidad ya retirada. Se limpian al arrancar:
// un navegador que las tenga no las arrastra para siempre. Idempotente.
//
// T16 (2026-10-02): jax_pending_cmds guardaba las tareas pendientes del modo Comando
// (/command), retirado.
const CLAVES_RETIRADAS = ['jax_pending_cmds']

export function limpiarRestosRetirados() {
  for (const clave of CLAVES_RETIRADAS) {
    try {
      localStorage.removeItem(clave)
    } catch (err) {
      // fail-soft: sin acceso a localStorage (modo privado, cuota) no hay nada que limpiar y
      // arrancar importa más; el resto de la app ya tolera ese caso.
      console.warn('no se pudo limpiar', clave, err)
    }
  }
}
