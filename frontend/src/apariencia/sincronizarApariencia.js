import api from '../api/client'
import { useTema } from '../store/useTema'
import { useApariencia } from '../store/useApariencia'

// UNA petición por carga para todo lo público de la instancia (frente C,
// 2026-09-16): tema, idioma y nombre. Rechaza si falla: quien llama decide
// (App y AdminSettings se quedan con los últimos conocidos).
export async function sincronizarApariencia() {
  const { data } = await api.get('/apariencia')
  useTema.getState().fijarPredeterminado(data?.theme_default)
  useApariencia.getState().fijar(data)
}
