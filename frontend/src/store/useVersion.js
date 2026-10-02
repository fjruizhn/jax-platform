import { useEffect } from 'react'
import { create } from 'zustand'
import api from '../api/client'

// Versión de Axioma, pedida en tiempo de ejecución a GET /api/version (sin
// autenticación; el backend la lee de VERSION al arrancar). Se pide UNA vez y se
// cachea en el store. Mientras carga, o si falla, `version` es null: quien la
// muestra pone solo el nombre, nunca un número de respaldo. Si falla, el
// próximo montaje vuelve a intentar. Sin invalidación: la versión solo cambia
// al reiniciar el backend, y un reinicio recarga la página con el sitio.
let pedido = null

export const useVersionStore = create(() => ({ version: null }))

export function pedirVersion() {
  if (pedido) return pedido
  pedido = api.get('/version')
    .then(({ data }) => {
      const v = data?.version
      if (typeof v === 'string' && v.trim() !== '') useVersionStore.setState({ version: v })
      else pedido = null
    })
    .catch(() => { pedido = null })
  return pedido
}

export function reiniciarVersion() {
  pedido = null
  useVersionStore.setState({ version: null })
}

export function useVersion() {
  const version = useVersionStore((s) => s.version)
  useEffect(() => { pedirVersion() }, [])
  return version
}
