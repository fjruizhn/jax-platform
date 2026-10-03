import { useLocation } from 'react-router-dom'

// Proyectos vive en dos sitios con los MISMOS componentes: dentro del
// caparazon de Administracion (/admin/proyectos...) y suelto (/proyectos...)
// para quien no es admin. El contexto se detecta por la ruta: no hay prop que
// olvidar al montar, y la URL es la unica verdad de donde se esta.
export function useBaseProyectos() {
  const { pathname } = useLocation()
  const dentroDeAdmin = pathname === '/admin/proyectos' || pathname.startsWith('/admin/proyectos/')
  return { dentroDeAdmin, base: dentroDeAdmin ? '/admin/proyectos' : '/proyectos' }
}
