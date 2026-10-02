import { useEffect } from 'react'
import { useI18n } from '../i18n/index.jsx'
import { useNombreDelSistema } from '../store/useApariencia'

// Título de la pestaña y meta description en tiempo de ejecución (Principio IV):
// plantilla i18n + nombre configurado (system_name, o brandName de respaldo).
// Lo que trae index.html es solo el respaldo inicial, antes de que cargue la app.
export default function TituloDePagina() {
  const { t } = useI18n()
  const nombre = useNombreDelSistema(t)
  useEffect(() => {
    document.title = t.tituloPagina(nombre)
    document.querySelector('meta[name="description"]')?.setAttribute('content', t.metaDescripcion(nombre))
  }, [t, nombre])
  return null
}
