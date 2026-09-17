// Lectura/escritura fail-soft de localStorage (frente C, revisión R11,
// 2026-09-17). Compartido por useTema.js y useApariencia.js: con el
// almacenamiento bloqueado (Safari con cookies bloqueadas, iframe con
// sandbox), localStorage.getItem/setItem lanzan SecurityError. Sin este
// try/catch, importar cualquiera de esos módulos tira y la pantalla de
// Login queda en blanco; con él, el valor sigue funcionando en memoria para
// esta carga, simplemente no persiste.
export function leer(clave) {
  try {
    return localStorage.getItem(clave)
  } catch {
    return null
  }
}

export function escribir(clave, valor) {
  try {
    localStorage.setItem(clave, valor)
  } catch {
    // fail-soft: sin almacenamiento, el valor sigue en memoria esta carga
  }
}
