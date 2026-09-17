import { useEffect, useRef, useState } from 'react'

// Confirmación de costo abierta SOBRE otro modal (Task 9, compartido desde la
// Task 10 por PipelineModal y ContinuarPipelineModal). Concentra lo que los
// dos necesitan igual:
//
// - `pendiente`: lo que espera confirmación ({veredicto, aviso, ...lo que el
//   llamador necesite para mandar exactamente lo que se confirmó}) o null.
// - `bloqueado` + `siLibre(fn)`: con la confirmación abierta el modal padre
//   queda congelado (fix round 1 Task 9). Dialogo sólo vuelve inert a #root y
//   los dos diálogos son portales hermanos, así que el padre lleva `inert` en
//   su contenido y cada acción se niega además en JS (jsdom ignora inert).
// - `conGuardia(accion)`: guardia SÍNCRONA contra el doble clic (adenda Task 9
//   ítem 5). `enviando` deshabilita el botón recién en el próximo render; dos
//   clics seguidos llegan antes y mandarían dos pedidos.
// - `botonPrincipalRef`: el commit que abre la confirmación vuelve inert al
//   envoltorio del botón enfocado, así que Dialogo registra `body` como
//   elemento previo y no devuelve el foco. Cuando la confirmación se cierra y
//   el padre sigue abierto, el foco va explícitamente a este botón (este
//   efecto corre después del cleanup de Dialogo y con el inert ya quitado).
export function useConfirmacionDeCosto() {
  const [pendiente, setPendiente] = useState(null)
  const [enviando, setEnviando] = useState(false)
  const enviandoRef = useRef(false)
  const botonPrincipalRef = useRef(null)
  const pendienteAnteriorRef = useRef(null)

  useEffect(() => {
    const habiaPendiente = pendienteAnteriorRef.current !== null
    pendienteAnteriorRef.current = pendiente
    if (habiaPendiente && pendiente === null) botonPrincipalRef.current?.focus()
  }, [pendiente])

  const bloqueado = pendiente !== null
  const siLibre = (fn) => (...args) => { if (!bloqueado) fn(...args) }

  // Corre `accion` si no hay otra en curso; el estado `enviando` vuelve a
  // false pase lo que pase (try/finally).
  async function conGuardia(accion) {
    if (enviandoRef.current) return
    enviandoRef.current = true
    setEnviando(true)
    try {
      await accion()
    } finally {
      enviandoRef.current = false
      setEnviando(false)
    }
  }

  return {
    pendiente,
    abrirConfirmacion: setPendiente,
    cerrarConfirmacion: () => setPendiente(null),
    enviando,
    enviandoRef,
    bloqueado,
    siLibre,
    conGuardia,
    botonPrincipalRef,
  }
}
