import { useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import Dialogo from '../Dialogo'
import { TAMANO_BOTON_44 } from '../../tema/botones'

// OP 358 pieza 4 (hub 2D): la galería ya no es un catálogo estático — el
// registro de salas (ids, estados, orden) lo anuncia el servidor y este
// componente sólo lo renderiza. Las salas activas abren los proyectos reales
// de su vertical (onAbrirVertical); las demás siguen informativas en Dialogo.
// Cada sala necesita presencia en ambos lados (registro del servidor + VISUAL
// e i18n aquí) para aparecer: un backend adelantado al frontend nunca rompe
// el hub, y una sala sin visual declarado no se renderiza a medias.
const VISUAL = {
  junta: { glifo: '▣' },
  agencia: { imagen: '/salas/agencia.jpg' },
  bufete: { glifo: '§' },
  arquitectura: { glifo: '△' },
  contable: { glifo: '∑' },
  banca: { glifo: '↗' },
  biblioteca: { glifo: '≡' },
}

export default function HubOficina({ verticales = [], onAbrirVertical }) {
  const { t } = useI18n()
  const [abierta, setAbierta] = useState(null)
  const textos = abierta ? t.virtualOffice.hub.salas[abierta.id] : null
  const salas = (verticales || []).filter((sala) => VISUAL[sala.id] && t.virtualOffice.hub.salas[sala.id])

  return (
    <section className="tarjeta-oficina">
      <h2 className="text-base font-semibold">{t.virtualOffice.hub.title}</h2>
      <p className="mt-1 text-sm text-texto-suave">{t.virtualOffice.hub.intro}</p>
      <ul className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        {salas.map((sala) => {
          const salaTextos = t.virtualOffice.hub.salas[sala.id]
          return (
            <li key={sala.id}>
              <button
                type="button"
                onClick={() => (sala.estado === 'activa' ? onAbrirVertical?.(sala.id) : setAbierta(sala))}
                aria-haspopup={sala.estado === 'activa' ? undefined : 'dialog'}
                className="sala-boton"
              >
                <span className="sala-visu">
                  {VISUAL[sala.id].imagen
                    ? <img src={VISUAL[sala.id].imagen} alt={salaTextos.alt} loading="lazy" className="sala-foto" />
                    : <span aria-hidden="true" className="sala-glifo">{VISUAL[sala.id].glifo}</span>}
                  <span className="sala-estado" data-estado={sala.estado}>{t.virtualOffice.hub.estados[sala.estado] || sala.estado}</span>
                </span>
                <span className="sala-pie">{salaTextos.nombre}</span>
              </button>
            </li>
          )
        })}
      </ul>
      {textos && (
        <Dialogo idTitulo="office-sala-detalle" titulo={textos.nombre} onCerrar={() => setAbierta(null)}>
          <p className="text-sm text-texto-suave">{textos.descripcion}</p>
          <p className="mt-3 text-xs font-semibold uppercase tracking-widest text-acento-texto">
            {t.virtualOffice.hub.estados[abierta.estado] || abierta.estado}
          </p>
          <div className="mt-5 flex justify-end">
            <button type="button" onClick={() => setAbierta(null)} className={`${TAMANO_BOTON_44} rounded bg-accion text-sobre-color`}>{t.virtualOffice.close}</button>
          </div>
        </Dialogo>
      )}
    </section>
  )
}
