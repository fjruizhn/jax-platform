import { create } from 'zustand'
import api from '../api/client'
import { useJaxStore } from './useJaxStore'

// Modo Ejecutor (SP2, 2026-09-17). Store propio: el Ejecutor no es el chat ni
// el modo Comando (tareas autónomas de Hyde). Contrato HTTP: /api/ejecutor/*,
// sólo superadmin. Quien pinta traduce los códigos; acá se guardan los datos y
// los errores tal cual (nunca se tragan).

// Cada cuánto se relee el detalle y la bitácora mientras la misión está en_curso.
export const INTERVALO_POLLING_MS = 2000
// Cuántas misiones recientes pide la lista.
export const LIMITE_MISIONES = 20

const INICIAL = {
  activo: false,
  estado: null,
  errorEstado: null,
  misiones: [],
  errorMisiones: null,
  idActiva: null,
  misionActiva: null,
  bitacora: [],
  errorMision: null,
  seleccion: [],
  enviando: false,
  errorEnvio: null,
}

// Fuera del estado de zustand: un temporizador no es un dato que se pinte.
let temporizador = null
let enVuelo = false

export const useEjecutor = create((set, get) => {
  // Misma regla que useJaxStore (_sessionEpoch): una respuesta que vuelve
  // después de un logout, o de un login de otra persona, no se escribe.
  const epoca = () => useJaxStore.getState()._sessionEpoch
  const vigente = (e) => epoca() === e

  return {
    ...INICIAL,

    setActivo: (activo) => {
      if (!activo) get().detenerPolling()
      set({ activo })
    },

    reiniciar: () => {
      get().detenerPolling()
      set(INICIAL)
    },

    cargarEstado: async () => {
      const e = epoca()
      try {
        const { data } = await api.get('/ejecutor/estado')
        if (!vigente(e)) return
        // La selección sólo conserva máquinas que siguen siendo elegibles.
        const elegibles = new Set((data.maquinas || []).filter((m) => m.elegible).map((m) => m.nombre))
        set((s) => ({ estado: data, errorEstado: null, seleccion: s.seleccion.filter((n) => elegibles.has(n)) }))
      } catch (err) {
        if (vigente(e)) set({ errorEstado: err })
      }
    },

    cargarMisiones: async () => {
      const e = epoca()
      try {
        const { data } = await api.get('/ejecutor/misiones', { params: { limite: LIMITE_MISIONES } })
        if (vigente(e)) set({ misiones: data.misiones || [], errorMisiones: null })
      } catch (err) {
        if (vigente(e)) set({ errorMisiones: err })
      }
    },

    alternarMaquina: (nombre) =>
      set((s) => ({
        seleccion: s.seleccion.includes(nombre) ? s.seleccion.filter((n) => n !== nombre) : [...s.seleccion, nombre],
      })),

    nuevaMision: () => {
      get().detenerPolling()
      set({ idActiva: null, misionActiva: null, bitacora: [], errorMision: null, errorEnvio: null })
    },

    abrirMision: async (id) => {
      get().detenerPolling()
      set({ idActiva: id, misionActiva: null, bitacora: [], errorMision: null, errorEnvio: null })
      await get().refrescarMision(id)
    },

    // Relee detalle + bitácora. La bitácora de la misión que ya se está
    // mostrando se pide con ?desde=<último id> y se anexa sin duplicar; una
    // misión recién abierta (bitácora vacía) trae la lista completa.
    // Arranca el polling si la misión está en_curso
    // y lo corta cuando termina (y entonces relee /estado y la lista, que
    // cambian con el fin del turno).
    refrescarMision: async (id) => {
      const e = epoca()
      enVuelo = true
      try {
        const previa = get().bitacora
        const desde = previa.length ? previa[previa.length - 1].id : null
        const [detalle, bitacora] = await Promise.all([
          api.get(`/ejecutor/misiones/${id}`),
          desde === null
            ? api.get(`/ejecutor/misiones/${id}/bitacora`)
            : api.get(`/ejecutor/misiones/${id}/bitacora`, { params: { desde } }),
        ])
        if (!vigente(e) || get().idActiva !== id) return
        const mision = detalle.data
        const llegados = bitacora.data.eventos || []
        set((s) => {
          if (desde === null) return { misionActiva: mision, bitacora: llegados, errorMision: null }
          const vistos = new Set(s.bitacora.map((ev) => ev.id))
          return { misionActiva: mision, bitacora: [...s.bitacora, ...llegados.filter((ev) => !vistos.has(ev.id))], errorMision: null }
        })
        if (mision.estado === 'en_curso') {
          get().iniciarPolling(id)
        } else {
          // Una misión terminada cambia la pausa (C5 pudo ponerla al cerrar el turno) y el estado de
          // la lista. Se relee SIEMPRE, no sólo cuando había sondeo: visto en real 2026-09-17 11:23,
          // la pantalla mostraba «En curso» y «pausa no puesta» con el turno fallido y la pausa puesta.
          get().detenerPolling()
          get().cargarEstado()
          get().cargarMisiones()
        }
      } catch (err) {
        // Un fallo de lectura no corta el polling: el próximo tick reintenta.
        if (vigente(e) && get().idActiva === id) set({ errorMision: err })
      } finally {
        enVuelo = false
      }
    },

    iniciarPolling: (id) => {
      if (temporizador) return
      temporizador = setInterval(() => {
        if (enVuelo) return
        get().refrescarMision(id)
      }, INTERVALO_POLLING_MS)
    },

    detenerPolling: () => {
      if (temporizador) clearInterval(temporizador)
      temporizador = null
    },

    // Objetivo de una misión nueva, o instrucción del turno siguiente si la
    // misión abierta puede continuar. Devuelve true si el backend la aceptó.
    enviar: async (texto) => {
      const e = epoca()
      const abierta = get().misionActiva
      set({ enviando: true, errorEnvio: null })
      try {
        const { data } = abierta?.puede_continuar
          ? await api.post(`/ejecutor/misiones/${abierta.id}/turnos`, { instruccion: texto })
          : await api.post('/ejecutor/misiones', { objetivo: texto, maquinas: get().seleccion })
        if (!vigente(e)) return false
        get().detenerPolling()
        // Otra misión: la bitácora se pide completa.
        if (data.id !== get().idActiva) set({ bitacora: [] })
        set({ idActiva: data.id, misionActiva: data, errorMision: null })
        get().refrescarMision(data.id)
        get().cargarMisiones()
        get().cargarEstado()
        return true
      } catch (err) {
        if (vigente(e)) set({ errorEnvio: err })
        return false
      } finally {
        if (vigente(e)) set({ enviando: false })
      }
    },

    // Pausa del Ejecutor (NO el kill switch global). Si falla, se relee
    // /estado: un 500 de auditoría puede haber dejado la pausa escrita.
    ponerPausa: () => _pausa('/ejecutor/pausa/poner'),
    quitarPausa: () => _pausa('/ejecutor/pausa/quitar'),
  }

  async function _pausa(url) {
    const e = epoca()
    try {
      const { data } = await api.post(url)
      if (vigente(e)) set((s) => ({ estado: s.estado ? { ...s.estado, pausa: data } : s.estado }))
      return data
    } catch (err) {
      if (vigente(e)) await get().cargarEstado()
      throw err
    }
  }
})

// Cerrar sesión (o cualquier cambio de sesión) corta el polling y borra lo del
// Ejecutor: el siguiente usuario no hereda misiones ni selección.
useJaxStore.subscribe((s, previo) => {
  if (s._sessionEpoch !== previo._sessionEpoch) useEjecutor.getState().reiniciar()
})
