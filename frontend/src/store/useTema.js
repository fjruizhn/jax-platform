import { create } from 'zustand'
import {
  CLAVE_ELECCION, CLAVE_PREDETERMINADO, esTema, temaInicial, aplicarTema,
} from '../tema/aplicarTema'

// Tema de la app (spec 2026-09-14-tema-tokens §5). Store y no useState: lo
// usan apariencia/sincronizarApariencia.js (el predeterminado al montar) y
// BarraUsuario (el interruptor), y dos estados locales se desalinearían.
// Misma interfaz que el useTheme de antes ({ theme, toggleTheme }) más
// `predeterminado`.
// M-4 (revisión final del PR 1, 2026-09-14): con el almacenamiento bloqueado
// (Safari con cookies bloqueadas, iframe con sandbox), localStorage.getItem/
// setItem lanzan SecurityError. `leer`/`escribir` son fail-soft: si el
// almacenamiento no responde, el tema sigue funcionando en memoria para esta
// carga (no hay pantalla en blanco), simplemente no persiste.
function leer(clave) {
  try {
    return localStorage.getItem(clave)
  } catch {
    return null
  }
}

function escribir(clave, valor) {
  try {
    localStorage.setItem(clave, valor)
  } catch {
    // fail-soft: sin almacenamiento, el cambio de tema sigue en memoria
  }
}

function predeterminadoGuardado() {
  const v = leer(CLAVE_PREDETERMINADO)
  return esTema(v) ? v : null
}

export const useTema = create((set, get) => ({
  theme: temaInicial(),
  predeterminado: predeterminadoGuardado(),

  // El interruptor escribe una ELECCIÓN: desde ahí el predeterminado no la pisa.
  toggleTheme: () => {
    const siguiente = get().theme === 'dark' ? 'light' : 'dark'
    escribir(CLAVE_ELECCION, siguiente)
    aplicarTema(siguiente)
    set({ theme: siguiente })
  },

  // Guarda el último predeterminado conocido (lo lee el script de index.html
  // en la próxima carga) y lo aplica sólo si el usuario nunca eligió.
  // Un valor fuera de {'dark','light'} no se guarda ni se aplica.
  fijarPredeterminado: (valor) => {
    if (!esTema(valor)) return
    escribir(CLAVE_PREDETERMINADO, valor)
    set({ predeterminado: valor })
    if (!esTema(leer(CLAVE_ELECCION)) && get().theme !== valor) {
      aplicarTema(valor)
      set({ theme: valor })
    }
  },

  // Decisión de Fernando (2026-09-14, fix vivo del PR 1): guardar el
  // predeterminado desde Configuración fija también la elección del propio
  // admin, aunque ya tuviera otra -- distinto de fijarPredeterminado, que
  // nunca pisa una elección existente. Sólo la llama AdminSettings.handleSave.
  fijarPredeterminadoComoEleccion: (valor) => {
    if (!esTema(valor)) return
    escribir(CLAVE_PREDETERMINADO, valor)
    escribir(CLAVE_ELECCION, valor)
    aplicarTema(valor)
    set({ predeterminado: valor, theme: valor })
  },
}))
