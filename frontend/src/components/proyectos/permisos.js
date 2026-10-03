// Quién puede tocar los documentos de un proyecto (E2a, T10). Es la decisión de B9
// (`memory:project:write` → CONTRIBUTOR, REVIEWER y OWNER) y vive aquí UNA vez: la
// pestaña, el detalle y cualquier pantalla que ofrezca subir, ocultar o restaurar
// preguntan a estas funciones. El servidor sigue siendo la autoridad; esto solo
// evita ofrecer lo que va a fallar.
const PAPELES_DE_ESCRITURA = ['CONTRIBUTOR', 'REVIEWER', 'OWNER']

// Ver la lista de ocultos exige el mismo papel que ocultar (el backend responde 403
// a un VIEWER), aunque el proyecto esté archivado.
export function puedeVerOcultos(proyecto) {
  return PAPELES_DE_ESCRITURA.includes(proyecto?.papel)
}

// Subir, ocultar y restaurar exigen además el proyecto ACTIVE (si no, 409).
export function puedeModificarDocumentos(proyecto) {
  return puedeVerOcultos(proyecto) && proyecto?.estado === 'ACTIVE'
}
