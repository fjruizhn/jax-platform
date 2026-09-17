import { create } from 'zustand'
import { CLAVE_IDIOMA_PREDETERMINADO, esIdioma } from '../i18n/idioma'
import { leer, escribir } from './almacenamiento'

// Nombre del sistema e idioma predeterminado (frente C, 2026-09-16), desde
// GET /apariencia (apariencia/sincronizarApariencia.js). Sin import de
// api/client a propósito: I18nProvider usa este store y lo renderizan casi
// todos los tests. El último valor conocido se guarda en este navegador para
// titular la pestaña antes de la respuesta; `leer`/`escribir` (fail-soft en
// localStorage: Safari con cookies bloqueadas, iframe con sandbox) están en
// almacenamiento.js, compartidas con useTema.js.
export const CLAVE_NOMBRE = 'jax_system_name'

export function esNombre(valor) {
  return typeof valor === 'string' && valor.trim() !== ''
}

function titular(nombre) {
  if (esNombre(nombre)) document.title = nombre
}

const nombreGuardado = leer(CLAVE_NOMBRE)
const idiomaGuardado = leer(CLAVE_IDIOMA_PREDETERMINADO)
titular(nombreGuardado)

export const useApariencia = create((set) => ({
  systemName: esNombre(nombreGuardado) ? nombreGuardado : null,
  langDefault: esIdioma(idiomaGuardado) ? idiomaGuardado : null,

  // Lo que no pasa la validación no se aplica: se queda el último conocido.
  fijar: (data) => {
    const cambios = {}
    if (esNombre(data?.system_name)) {
      escribir(CLAVE_NOMBRE, data.system_name)
      titular(data.system_name)
      cambios.systemName = data.system_name
    }
    if (esIdioma(data?.lang_default)) {
      escribir(CLAVE_IDIOMA_PREDETERMINADO, data.lang_default)
      cambios.langDefault = data.lang_default
    }
    set(cambios)
  },
}))

// El nombre que muestra la UI. Antes de conocer el del servidor (primera
// visita, sin red), la marca de i18n: es lo que se mostraba hasta el frente C.
export function useNombreDelSistema(t) {
  const nombre = useApariencia((s) => s.systemName)
  return nombre ?? t.brandName
}
