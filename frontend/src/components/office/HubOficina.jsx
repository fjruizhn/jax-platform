import { useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import Dialogo from '../Dialogo'
import { TAMANO_BOTON_44 } from '../../tema/botones'

// OP 358 pieza 3 (hub 2D): la galería de salas de la oficina central. Cada
// tarjeta es una vertical del despacho; hoy solo la Agencia tiene sala 3D
// construida y por eso es la única con captura real (public/salas/agencia.jpg,
// servida como asset estático — mismo patrón que favicon.svg). Las demás
// salas muestran una composición honesta de tokens con su glifo y su estado.
// El clic abre el detalle en Dialogo: todavía no hay destino detrás (el
// enganche de verticales llega con la pieza siguiente), y el catálogo es
// estático hasta que esa pieza defina el registro en el backend.
const SALAS = [
  { id: 'junta', estado: 'proximamente', glifo: '▣' },
  { id: 'agencia', estado: 'activa', imagen: '/salas/agencia.jpg' },
  { id: 'bufete', estado: 'proximamente', glifo: '§' },
  { id: 'arquitectura', estado: 'proximamente', glifo: '△' },
  { id: 'contable', estado: 'proximamente', glifo: '∑' },
  { id: 'banca', estado: 'proximamente', glifo: '↗' },
  { id: 'biblioteca', estado: 'futura', glifo: '≡' },
]

export default function HubOficina() {
  const { t } = useI18n()
  const [abierta, setAbierta] = useState(null)
  const textos = abierta ? t.virtualOffice.hub.salas[abierta.id] : null

  return (
    <section className="tarjeta-oficina">
      <h2 className="text-base font-semibold">{t.virtualOffice.hub.title}</h2>
      <p className="mt-1 text-sm text-texto-suave">{t.virtualOffice.hub.intro}</p>
      <ul className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        {SALAS.map((sala) => {
          const salaTextos = t.virtualOffice.hub.salas[sala.id]
          return (
            <li key={sala.id}>
              <button type="button" onClick={() => setAbierta(sala)} aria-haspopup="dialog" className="sala-boton">
                <span className="sala-visu">
                  {sala.imagen
                    ? <img src={sala.imagen} alt={salaTextos.alt} loading="lazy" className="sala-foto" />
                    : <span aria-hidden="true" className="sala-glifo">{sala.glifo}</span>}
                  <span className="sala-estado" data-estado={sala.estado}>{t.virtualOffice.hub.estados[sala.estado]}</span>
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
            {t.virtualOffice.hub.estados[abierta.estado]}
          </p>
          <div className="mt-5 flex justify-end">
            <button type="button" onClick={() => setAbierta(null)} className={`${TAMANO_BOTON_44} rounded bg-accion text-sobre-color`}>{t.virtualOffice.close}</button>
          </div>
        </Dialogo>
      )}
    </section>
  )
}
