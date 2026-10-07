import { useCallback, useEffect, useRef, useState } from 'react'
import { useJaxStore } from '../../store/useJaxStore'
import { useI18n } from '../../i18n/index.jsx'
import { listarProyectos } from '../../api/proyectos'
import { TAMANO_BOTON_ACCION } from '../../tema/botones'

// Selector «Personal / proyecto» del chat (E1, T9). Control nativo <select>:
// teclado y lector de pantalla gratis. E1.1 (Fernando, 2026-10-02): vive DENTRO
// de la fila de modos, pegado a Chat, con el mismo tamaño y tipografía que los
// botones de modo (objetivo táctil = el de sus vecinos, 24 px de alto mínimo).
// 2026-10-02 (Fernando, con captura): se veía pegado a Chat y del color de los
// botones. Ahora va separado (margen + divisor sutil, y aire tras el 📁) y con
// el token de superficie más clara (superficie-2, no el bg-superficie de los
// botones de modo), para que se distinga que es otra cosa.
//
// El proyecto elegido vive SOLO en memoria (store). No se persiste entre
// recargas: el mecanismo de `store/almacenamiento` guarda preferencias del
// NAVEGADOR (tema, idioma), no del usuario; en un navegador compartido el
// proyecto de una persona se le ofrecería a la siguiente. Tras recargar, el
// chat arranca en «Personal», que es lo seguro.
//
// La lista se pide al montar y cada vez que se abre. Si el proyecto elegido ya
// no está (archivado, o ya no se es miembro), se vuelve a «Personal» con aviso.
// Si la lista falla, no se des-elige nada: el 403 `project_scope_denied` del
// chat es la defensa real.
export default function SelectorDeProyecto({ bloqueado = false }) {
  const { t } = useI18n()
  const proyectoActivo = useJaxStore((s) => s.proyectoActivo)
  const setProyectoActivo = useJaxStore((s) => s.setProyectoActivo)
  const addToast = useJaxStore((s) => s.addToast)
  const [proyectos, setProyectos] = useState([])
  const pedidoRef = useRef(0)
  const montado = useRef(true)

  const cargar = useCallback(async () => {
    const pedido = ++pedidoRef.current
    try {
      const r = await listarProyectos({ vista: 'activos', limite: 100 })
      if (!montado.current || pedido !== pedidoRef.current) return
      const lista = Array.isArray(r?.proyectos) ? r.proyectos : []
      setProyectos(lista)
      const elegido = useJaxStore.getState().proyectoActivo
      if (elegido && !lista.some((p) => p.id === elegido.id)) {
        useJaxStore.getState().setProyectoActivo(null)
        useJaxStore.getState().addToast({ type: 'warning', message: t.proyectos.proyectoNoDisponible })
      }
    } catch {
      // sin lista no se decide nada (ver cabecera)
    }
  }, [t])

  useEffect(() => {
    montado.current = true
    cargar()
    return () => { montado.current = false }
  }, [cargar])

  function elegir(e) {
    if (bloqueado) return
    const id = e.target.value
    if (id === '') { setProyectoActivo(null); return }
    const p = proyectos.find((x) => String(x.id) === id)
    if (p) setProyectoActivo(p)
  }

  // Un proyecto elegido que aún no aparece en la lista (recién recargada) se
  // muestra igual, para que el control no mienta sobre lo que se va a enviar.
  const hayElegidoFueraDeLista = proyectoActivo && !proyectos.some((p) => p.id === proyectoActivo.id)

  return (
    <span className="inline-flex items-center ml-2 pl-3 border-l border-borde">
      <label htmlFor="selector-proyecto-chat" className="sr-only">
        {t.proyectos.selectorChat.etiqueta}
      </label>
      <span aria-hidden="true" className="text-xs mr-1.5 select-none">📁</span>
      <select
        id="selector-proyecto-chat"
        value={proyectoActivo ? String(proyectoActivo.id) : ''}
        disabled={bloqueado}
        onChange={elegir}
        onMouseDown={cargar}
        onFocus={cargar}
        className={`${TAMANO_BOTON_ACCION} max-w-40 truncate rounded bg-superficie-2 text-texto-suave hover:text-texto font-semibold focus:outline-none focus-visible:ring-2 focus-visible:ring-foco disabled:opacity-60 disabled:cursor-not-allowed`}
      >
        <option value="">{t.proyectos.selectorChat.personal}</option>
        {hayElegidoFueraDeLista && (
          <option value={String(proyectoActivo.id)}>{proyectoActivo.nombre}</option>
        )}
        {proyectos.map((p) => (
          <option key={p.id} value={String(p.id)}>{p.nombre}</option>
        ))}
      </select>
    </span>
  )
}
