import { useCallback, useEffect, useRef, useState } from 'react'
import { useI18n } from '../i18n/index.jsx'
import { useNombreDelSistema } from '../store/useApariencia'
import { useJaxStore } from '../store/useJaxStore'
import api from '../api/client'
import { codigoDe } from '../api/errores'
import Dialogo from '../components/Dialogo'
import ConfirmacionSuma from '../components/ConfirmacionSuma'
import GrupoDeHechos from '../components/Memoria/GrupoDeHechos'
import SeccionVencidos from '../components/Memoria/SeccionVencidos'

// Pantalla de Memoria (Task 6, plan 2026-09-20-memoria-admin.md; spec
// 2026-09-18-memoria-admin-design.md). El criterio de éxito del spec §6 es de
// USO, no de código: "Fernando abre la pantalla y en un rato deja los 116
// hechos revisados. Si revisar 116 se siente imposible, la pantalla está mal
// hecha." De ahí salen las tres decisiones de esta pantalla:
//   1. Aprobar es la única acción SIN ventana propia -- es la que se hace
//      116 veces, y no es destructiva (spec §2.2). Corregir y caducar sí
//      llevan ventana propia y, por ser destructivas (Global Constraints del
//      plan), ConfirmacionSuma antes de escribir.
//   2. El lote se arma DESDE EL GRUPO (GrupoDeHechos), no hecho por hecho:
//      "revisar 115 de a uno no lo hace nadie" (spec §2.2).
//   3. Los hechos que forman parte de un casi-duplicado (grupo.casi_duplicados,
//      Task 5) arrancan SIN seleccionar en el lote por defecto -- aprobar el
//      grupo entero de un click aprobaría a la vez tres redacciones que dicen
//      lo mismo, exactamente el problema que la pantalla viene a resolver. Se
//      resuelven con el botón "Fundir" del cluster o a mano, ficha por ficha.
//
// GET /grupos (Task 5) sólo trae ids; esta pantalla junta esos ids con
// GET /hechos (Task 3, límite máximo del backend: 500) para tener el dato
// completo de cada uno. Los dos excluyen vencidos y superados por defecto --
// coinciden, así que todo id de un grupo se encuentra siempre en
// `hechosPorId`.
//
// Fundir (decisión de Fernando, 2026-09-20): SUPERA, no caduca -- "esto fue
// reemplazado POR AQUELLO", no "esto dejó de valer". Tres hechos que dicen
// lo mismo no son tres hechos vencidos: son uno con tres redacciones, y
// `superseded_by` (POST /hechos/fundir) reconstruye esa cadena, cosa que
// `expires_at` no puede. El más reciente del cluster se aprueba aparte
// (POST /hechos/aprobar, igual que antes); el resto queda superado por él.
//
// GET /grupos arma sus clusters SÓLO con hechos activos (`superseded_by IS
// NULL AND (expires_at IS NULL OR expires_at > NOW())`, ver
// backend/api/admin/memoria.py::SQL_ACTIVOS_CON_VECTOR): un hecho ya vencido
// no aparece en ningún grupo. Por eso la sección Vencidos (abajo del todo,
// SeccionVencidos.jsx) pide `incluir_vencidos=true` por separado y filtra
// del lado del cliente -- es la única forma de volver a VER y de quitarle la
// caducidad a un hecho que caducó en una sesión anterior (sin esto, caducar
// por error y recargar la pantalla era, en la práctica, borrar).
//
// Corrección (2026-09-20, observación de Fernando): esta pantalla vivía como
// ruta suelta de App.jsx, hermana de /admin/*, SIN el caparazón de
// Administración -- por eso montaba su propio <Toast/> y un enlace "Volver a
// Axioma" al inicio (la única salida posible). Ahora es una ruta anidada más
// de Admin.jsx (ver Admin.jsx y AdminSidebar.jsx), que ya provee las dos
// cosas: la barra lateral es la salida (ninguna de las otras 7 pantallas de
// admin lleva un "volver" propio -- mismo patrón acá) y <Toast/> ya está
// montado una vez en Admin.jsx; montarlo también acá duplicaría el overlay
// fijo de avisos. El wrapper externo (min-h-dvh/bg-fondo/p-6) también salió:
// ahora vive dentro del <main> de Admin.jsx, que ya pone su propio fondo y
// padding -- doble padding no es un detalle menor cuando el contenido tiene
// que caber en el panel central, no en toda la pantalla.
const emptySet = () => new Set()

function mensajeDeError(t, err) {
  const code = codigoDe(err)
  return (code && t.memoria.errores[code]) || t.memoria.errorGenerico
}

export default function Memoria() {
  const { t } = useI18n()
  const nombre = useNombreDelSistema(t)
  const addToast = useJaxStore((s) => s.addToast)

  const [cargando, setCargando] = useState(true)
  // MAJOR B (revisión adversarial de jax-platform PR 146, ronda 4): la
  // primera carga usa `cargando` (pantalla completa, sin nada que mostrar
  // todavía) -- las recargas que siguen a una acción (aprobar/caducar/
  // corregir/fundir, ver `cargar()` más abajo) usan `recargando`, que NO
  // oculta la lista: Fernando no puede perder de vista lo que ya estaba
  // revisando cada vez que aprueba un hecho suelto.
  const [recargando, setRecargando] = useState(false)
  const yaSeCargoAlgunaVez = useRef(false)
  const [error, setError] = useState(false)
  const [grupos, setGrupos] = useState([])
  const [hechosPorId, setHechosPorId] = useState({})
  const [vencidos, setVencidos] = useState([])
  // Verdad de escala (M. Ruiz, 2026-09-20): a 116 hechos, "lo que se cargó"
  // y "lo que hay" coinciden y nadie nota la diferencia. A 10.000 no --
  // medido con la base de carga: la cabecera decía "467 sin verificar"
  // mientras la base tenía 8.000, porque contaba sólo los ids que entraron
  // en el límite de 500 de GET /hechos. `totalSinVerificarReal` guarda lo
  // que la base tiene DE VERDAD (GET /hechos ya devuelve `total`, contado
  // en el servidor con SQL_CONTAR -- no hace falta traer las filas para
  // saber cuántas hay). `vencidosTotalReal` sale de la misma idea sin pedir
  // un tercer endpoint: `rVencidos.total` (activos+vencidos) menos
  // `rHechos.total` (sólo activos) es, por construcción de SQL_CONTAR, la
  // cuenta de vencidos -- las dos comparten el mismo filtro salvo
  // `incluir_vencidos`.
  const [totalSinVerificarReal, setTotalSinVerificarReal] = useState(0)
  const [vencidosTotalReal, setVencidosTotalReal] = useState(0)
  const [seleccionados, setSeleccionados] = useState(emptySet)
  // MAJOR B: ids que YA estaban en pantalla en la carga anterior -- para
  // que `cargar()` sepa distinguir "id nuevo, aplicale el default" de "id
  // conocido, respetá lo que Fernando ya marcó/desmarcó a mano". `useRef`,
  // no estado: se actualiza dentro del mismo ciclo de `cargar()`, no
  // necesita disparar un render propio.
  const idsConocidosRef = useRef(new Set())
  const [procesando, setProcesando] = useState(emptySet)
  // MAJOR N1 (revisión adversarial de jax-platform PR 146, ronda 5): con la
  // lista ya visible durante una recarga (MAJOR B, ronda 4), dos `cargar()`
  // pueden quedar en vuelo a la vez -- una acción dispara la siguiente antes
  // de que la anterior responda. Si la primera (más vieja) tarda más y
  // llega DESPUÉS de la segunda, pisa el estado nuevo con uno vencido (un
  // hecho que la segunda ya sacó de pantalla vuelve a aparecer). Cada
  // llamada a `cargar()` se numera; sólo la respuesta de la ÚLTIMA llamada
  // emitida se aplica (a los datos y al apagado de "Actualizando…") --
  // mismo patrón que un AbortController, sin depender de que axios/el mock
  // de test soporte `signal`.
  const peticionRef = useRef(0)

  // Ventanas propias (Global Constraints: nunca los diálogos del navegador).
  // Sólo una a la vez, mismo patrón que AdminUsers/AdminSmtp.
  const [corrigiendo, setCorrigiendo] = useState(null) // { hecho, texto, err }
  const [confirmandoCorreccion, setConfirmandoCorreccion] = useState(null) // { hecho, texto }
  const [caducando, setCaducando] = useState(null) // hecho
  const [fundiendo, setFundiendo] = useState(null) // { ids, supervivienteId }

  const cargar = useCallback(async () => {
    // MAJOR N1: esta llamada se numera -- si para cuando responde ya salió
    // una más nueva, su resultado se descarta entero (ni pisa `grupos`/
    // `hechosPorId`/etc, ni apaga "Actualizando…").
    const miPeticion = ++peticionRef.current
    // MAJOR B: sólo la PRIMERA carga usa la pantalla completa de "Cargando…"
    // -- las recargas que siguen a una acción usan `recargando`, que la
    // lista ignora al decidir si se pinta (ver el JSX más abajo).
    if (yaSeCargoAlgunaVez.current) setRecargando(true)
    else setCargando(true)
    setError(false)
    try {
      const [rGrupos, rHechos, rVencidos, rSinVerificarReal] = await Promise.all([
        api.get('/admin/memoria/grupos'),
        api.get('/admin/memoria/hechos', { params: { limite: 500 } }),
        api.get('/admin/memoria/hechos', { params: { limite: 500, incluir_vencidos: true } }),
        // limite=1: no interesan las filas, sólo el `total` que ya calcula
        // SQL_CONTAR en el servidor -- pedir 500 filas para tirarlas sería
        // trabajo de más, no una cuenta más verdadera.
        api.get('/admin/memoria/hechos', { params: { verificado: false, limite: 1 } }),
      ])
      // MAJOR N1: si otra llamada más nueva ya se emitió mientras ésta
      // estaba en vuelo, esta respuesta es vieja -- no toca ningún estado.
      if (peticionRef.current !== miPeticion) return
      const porId = {}
      for (const h of rHechos.data.hechos) porId[h.id] = h
      // No se preseleccionan los casi-duplicados: ver nota de módulo, punto 3.
      // Ronda 2026-09-22: cada cluster es {ids, superviviente_id} (antes,
      // una lista de ids a secas) -- `.flat()` sobre objetos no aplanaba
      // nada y esto dejaba de excluir a los casi-duplicados de la
      // preselección. Hay que entrar por `.ids`.
      const idsDeCluster = new Set(
        rGrupos.data.grupos.flatMap((g) => (g.casi_duplicados || []).flatMap((c) => c.ids)),
      )
      setGrupos(rGrupos.data.grupos)
      setHechosPorId(porId)
      // incluir_vencidos=true trae vencidos Y activos juntos (ver nota de
      // módulo): acá se filtra sólo lo vencido, para la sección aparte.
      setVencidos(rVencidos.data.hechos.filter((h) => h.vencido))
      setTotalSinVerificarReal(rSinVerificarReal.data.total)
      setVencidosTotalReal(Math.max(0, rVencidos.data.total - rHechos.data.total))
      // MAJOR B (revisión adversarial de jax-platform PR 146, ronda 4): antes
      // esta línea rearmaba `seleccionados` DESDE CERO en cada recarga --
      // aprobar un hecho suelto (o cualquier otra acción, todas recargan
      // desde M2) volvía a marcar hechos que Fernando ya había desmarcado a
      // mano en OTRO grupo. Ahora: un id que ya estaba en pantalla en la
      // carga anterior (`idsConocidosRef`) CONSERVA su estado (marcado o
      // no, tal cual lo dejó Fernando); sólo un id NUEVO (nunca visto) recibe
      // el default de siempre. Un id que desapareció simplemente no entra al
      // nuevo Set -- sale solo.
      const idsConocidosAntes = idsConocidosRef.current
      const idsNuevos = new Set(rHechos.data.hechos.map((h) => h.id))
      setSeleccionados((prev) => {
        const siguiente = new Set()
        for (const h of rHechos.data.hechos) {
          if (idsConocidosAntes.has(h.id)) {
            if (prev.has(h.id)) siguiente.add(h.id)
          } else if (!h.verificado && !idsDeCluster.has(h.id)) {
            siguiente.add(h.id)
          }
        }
        return siguiente
      })
      idsConocidosRef.current = idsNuevos
    } catch {
      // MAJOR N1: un error de una llamada vieja tampoco pisa el estado --
      // si la última llamada en curso sigue viva, que sea ella la que
      // decida si hubo error.
      if (peticionRef.current === miPeticion) setError(true)
    } finally {
      // MAJOR N1: sólo la última llamada apaga "Cargando…"/"Actualizando…"
      // -- una vieja que responde tarde no puede reabrir esa pantalla ni
      // apagar el aviso de una recarga más nueva que sigue en vuelo.
      if (peticionRef.current === miPeticion) {
        if (yaSeCargoAlgunaVez.current) setRecargando(false)
        else setCargando(false)
        yaSeCargoAlgunaVez.current = true
      }
    }
  }, [])

  useEffect(() => { cargar() }, [cargar])

  function marcarProcesando(ids, ocupado) {
    setProcesando((prev) => {
      const next = new Set(prev)
      for (const id of ids) { if (ocupado) next.add(id); else next.delete(id) }
      return next
    })
  }

  function onToggleSeleccion(id) {
    setSeleccionados((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  function onSeleccionarTodos(ids) {
    setSeleccionados((prev) => new Set([...prev, ...ids]))
  }

  function onSeleccionarNinguno(ids) {
    setSeleccionados((prev) => {
      const next = new Set(prev)
      for (const id of ids) next.delete(id)
      return next
    })
  }

  // Aprobar: la única acción sin ventana propia (ver nota de módulo, punto 1).
  //
  // M2 (revisión adversarial de jax-platform PR 146, tercera vuelta): antes
  // actualizaba `hechosPorId` a mano (optimista) y ahí se quedaba -- pero
  // aprobar cambia `is_verified`, que es justo lo que decide quién sobrevive
  // en un cluster (`_elegir_superviviente`, backend/api/admin/memoria.py).
  // Sin recargar `/grupos`, la pantalla podía seguir mostrando como
  // "sobrevive" a un hecho que la regla real ya no elegiría -- el mismo
  // estado viejo en pantalla que fundir ya resolvía recargando. Ahora las
  // cuatro acciones que cambian el estado de un hecho (aprobar, caducar,
  // quitar caducidad, corregir) recargan igual que fundir.
  async function aprobar(ids) {
    if (!ids.length) return
    marcarProcesando(ids, true)
    try {
      const { data } = await api.post('/admin/memoria/hechos/aprobar', { ids })
      addToast({ type: 'success', message: t.memoria.aprobados(data.aprobados ?? ids.length) })
      await cargar()
    } catch (err) {
      addToast({ type: 'error', message: mensajeDeError(t, err) })
    } finally {
      marcarProcesando(ids, false)
    }
  }

  // Caducar: destructivo -> ConfirmacionSuma (paso 1: abrir).
  function abrirCaducar(hecho) {
    setCaducando(hecho)
  }

  async function confirmarCaducar() {
    const hecho = caducando
    marcarProcesando([hecho.id], true)
    try {
      const vence_at = new Date().toISOString()
      await api.post(`/admin/memoria/hechos/${hecho.id}/caducar`, { vence_at })
      setCaducando(null)
      addToast({ type: 'success', message: t.memoria.caducado })
      await cargar() // M2: un hecho caducado sale de todos los grupos -- ver nota de módulo.
    } catch (err) {
      addToast({ type: 'error', message: mensajeDeError(t, err) })
    } finally {
      marcarProcesando([hecho.id], false)
    }
  }

  // Quitar caducidad: restaura, no destruye nada -> sin ventana propia,
  // como aprobar.
  async function quitarCaducidad(id) {
    marcarProcesando([id], true)
    try {
      await api.post(`/admin/memoria/hechos/${id}/caducar`, { vence_at: null })
      addToast({ type: 'success', message: t.memoria.caducidadQuitada })
      await cargar() // M2: un hecho reactivado puede volver a aparecer en un grupo -- ver nota de módulo.
    } catch (err) {
      addToast({ type: 'error', message: mensajeDeError(t, err) })
    } finally {
      marcarProcesando([id], false)
    }
  }

  // Corregir: dos pasos (Global Constraints: destructivo -> ConfirmacionSuma;
  // pero necesita texto libre, que ConfirmacionSuma no ofrece). Paso 1: texto
  // en un Dialogo común. Paso 2: confirmar con la suma antes de escribir.
  function abrirCorregir(hecho) {
    setCorrigiendo({ hecho, texto: hecho.texto, err: null })
  }

  function enviarBorradorDeCorreccion(e) {
    e.preventDefault()
    const texto = corrigiendo.texto.trim()
    if (!texto) { setCorrigiendo((c) => ({ ...c, err: t.memoria.corregirVacio })); return }
    setConfirmandoCorreccion({ hecho: corrigiendo.hecho, texto })
    setCorrigiendo(null)
  }

  async function confirmarCorreccion() {
    const { hecho, texto } = confirmandoCorreccion
    marcarProcesando([hecho.id], true)
    try {
      await api.post(`/admin/memoria/hechos/${hecho.id}/corregir`, { texto })
      setConfirmandoCorreccion(null)
      addToast({ type: 'success', message: t.memoria.corregido })
      await cargar() // cambio estructural (superado_by + fact nuevo): recargar es más simple y confiable que reconstruir a mano.
    } catch (err) {
      addToast({ type: 'error', message: mensajeDeError(t, err) })
    } finally {
      marcarProcesando([hecho.id], false)
    }
  }

  // Fundir (casi-duplicados, spec §2.1: "un botón para fundirlos"): supera
  // el resto del cluster con el superviviente que YA ELIGIÓ el backend
  // (POST /grupos::casi_duplicados[].superviviente_id -- ver nota de módulo
  // y GrupoDeHechos.jsx). Ronda 2026-09-22 (hallazgo de Fernando): el
  // superviviente ya NO es "el más reciente" a secas -- si hay un
  // verificado en el cluster, ese gana, aunque sea más viejo; adivinarlo acá
  // duplicaría una regla que ya vive en el backend
  // (_elegir_superviviente). Por eso esta pantalla recibe el id elegido, no
  // lo calcula. Una sola llamada: `/hechos/fundir` aprueba al superviviente
  // (si hacía falta) y funde en la MISMA transacción -- ya no hay una
  // llamada aparte a `/hechos/aprobar` antes (esa ventana entre las dos
  // llamadas era justo lo que dejaba "fundir a medias" posible).
  //
  // Ronda 146 (D5): `supervivienteVerificado`/`supervivienteTexto` vienen
  // del cluster (GrupoDeHechos.jsx), NO de `hechosPorId` -- ese diccionario
  // sólo tiene los primeros 500 hechos que cargó GET /hechos, y un cluster
  // puede incluir ids que ese cap dejó afuera. El motivo del mensaje
  // (verificado vs. más reciente) y el texto de la ficha salen de ESTOS dos
  // campos, no de una búsqueda en `hechosPorId` que podría fallar en
  // silencio.
  function abrirFundir(ids, supervivienteId, supervivienteVerificado, supervivienteTexto) {
    setFundiendo({ ids, supervivienteId, supervivienteVerificado, supervivienteTexto })
  }

  async function confirmarFundir() {
    const { ids, supervivienteId } = fundiendo
    const absorbidos = ids.filter((id) => id !== supervivienteId)
    marcarProcesando(ids, true)
    try {
      await api.post('/admin/memoria/hechos/fundir', {
        superviviente_id: supervivienteId, absorbidos,
      })
      setFundiendo(null)
      addToast({ type: 'success', message: t.memoria.fundido })
      await cargar() // cambio estructural (superseded_by en varios hechos): recargar, igual que corregir.
    } catch (err) {
      addToast({ type: 'error', message: mensajeDeError(t, err) })
    } finally {
      marcarProcesando(ids, false)
    }
  }

  // Lo cargado (lo que hay fichas para aprobar en pantalla AHORA) puede ser
  // menos que lo real -- el cap de 500 de GET /hechos. Cuando coinciden (la
  // escala de hoy, 116 hechos) el texto es el de siempre, sin ruido nuevo;
  // cuando no, decirlo explícito es la diferencia entre informar y mentir
  // con confianza (mismo patrón que el 404 de "no encontrado" con la base
  // caída, y el /fact verify que no encontraba un hecho que sí existía).
  const totalSinVerificarCargados = Object.values(hechosPorId).filter((h) => !h.verificado).length

  return (
    <div>
      <div className="max-w-4xl mx-auto space-y-4">
        <div className="mb-2">
          <h1 className="text-xl font-bold text-texto-fuerte">{t.memoria.titulo}</h1>
          <p className="text-xs text-texto-tenue mt-0.5">{t.memoria.subtitulo(nombre)}</p>
        </div>

        {!cargando && !error && (
          <p className="text-xs text-texto-suave">
            {totalSinVerificarCargados === totalSinVerificarReal
              ? t.memoria.totalSinVerificar(totalSinVerificarReal)
              : t.memoria.totalSinVerificarSubconjunto(totalSinVerificarCargados, totalSinVerificarReal)}
          </p>
        )}

        {/* MAJOR B: aviso chico, NO bloqueante -- la lista sigue debajo,
            tal como estaba, mientras la recarga posterior a una acción
            todavía está en vuelo. */}
        {!cargando && recargando && (
          <p role="status" className="text-xs text-texto-tenue">{t.memoria.actualizando}</p>
        )}

        {cargando && <p className="text-sm text-texto-tenue">{t.memoria.cargando}</p>}
        {!cargando && error && (
          <div role="alert" className="text-sm text-peligro bg-peligro-fondo border border-peligro-borde rounded-lg px-3 py-2">
            {t.memoria.errorCarga}
          </div>
        )}
        {!cargando && !error && grupos.length === 0 && (
          <p className="text-sm text-texto-tenue">{t.memoria.vacio}</p>
        )}

        {!cargando && !error && grupos.map((grupo, indice) => (
          <GrupoDeHechos
            key={`${grupo.tema}-${indice}`}
            grupo={grupo}
            indice={indice}
            hechosPorId={hechosPorId}
            seleccionados={seleccionados}
            procesando={procesando}
            onToggleSeleccion={onToggleSeleccion}
            onSeleccionarTodos={onSeleccionarTodos}
            onSeleccionarNinguno={onSeleccionarNinguno}
            onAprobarLote={aprobar}
            onAprobar={(id) => aprobar([id])}
            onAbrirCorregir={abrirCorregir}
            onAbrirCaducar={abrirCaducar}
            onQuitarCaducidad={quitarCaducidad}
            onAbrirFundir={abrirFundir}
          />
        ))}

        {!cargando && !error && (
          <SeccionVencidos vencidos={vencidos} totalReal={vencidosTotalReal} procesando={procesando} onQuitarCaducidad={quitarCaducidad} />
        )}
      </div>

      {corrigiendo && (
        <Dialogo idTitulo="memoria-corregir-titulo" titulo={t.memoria.corregirTitulo(corrigiendo.hecho.id)} onCerrar={() => setCorrigiendo(null)}>
          <form onSubmit={enviarBorradorDeCorreccion} className="space-y-3">
            <div>
              <label htmlFor="memoria-corregir-texto" className="block text-xs text-texto-suave mb-1 font-semibold uppercase tracking-wider">
                {t.memoria.corregirTexto}
              </label>
              <textarea
                id="memoria-corregir-texto"
                rows={4}
                className="w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto placeholder-texto-tenue focus:outline-none focus:border-foco"
                value={corrigiendo.texto}
                onChange={(e) => setCorrigiendo((c) => ({ ...c, texto: e.target.value, err: null }))}
              />
            </div>
            {corrigiendo.err && (
              <div role="alert" className="text-sm text-peligro bg-peligro-fondo border border-peligro-borde rounded-lg px-3 py-2">
                {corrigiendo.err}
              </div>
            )}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setCorrigiendo(null)} className="px-3 py-1.5 rounded-lg text-sm text-texto-suave hover:text-texto transition-colors">
                {t.memoria.corregirCancelar}
              </button>
              <button type="submit" className="px-4 py-1.5 rounded-lg bg-acento hover:bg-acento-hover text-sobre-color text-sm font-semibold transition-colors">
                {t.memoria.corregirGuardar}
              </button>
            </div>
          </form>
        </Dialogo>
      )}

      {confirmandoCorreccion && (
        <ConfirmacionSuma
          titulo={t.memoria.corregirConfirmarTitulo}
          mensaje={t.memoria.corregirConfirmarMensaje}
          textoConfirmar={t.memoria.corregirConfirmarBoton}
          onConfirmar={confirmarCorreccion}
          onCancelar={() => setConfirmandoCorreccion(null)}
        />
      )}

      {caducando && (
        <ConfirmacionSuma
          titulo={t.memoria.caducarTitulo}
          mensaje={t.memoria.caducarMensaje}
          textoConfirmar={t.memoria.caducarConfirmar}
          onConfirmar={confirmarCaducar}
          onCancelar={() => setCaducando(null)}
        />
      )}

      {fundiendo && (
        <ConfirmacionSuma
          titulo={t.memoria.fundirTitulo(fundiendo.supervivienteId)}
          // M3 (revisión adversarial de jax-platform PR 146, tercera
          // vuelta): "la confirmación... cuenta TODOS los item.ids" -- si
          // algún miembro del cluster no está en `hechosPorId` (el cap de
          // 500 de GET /hechos lo dejó afuera), se lo dice explícito acá
          // también, no sólo en el aviso del panel (GrupoDeHechos.jsx).
          mensaje={t.memoria.fundirMensaje(fundiendo.supervivienteTexto, fundiendo.supervivienteVerificado)
            + (() => {
              const noCargados = fundiendo.ids.filter((id) => !hechosPorId[id]).length
              return noCargados > 0 ? ` ${t.memoria.casiDuplicadosNoCargados(noCargados)}` : ''
            })()}
          textoConfirmar={t.memoria.fundirConfirmar}
          onConfirmar={confirmarFundir}
          onCancelar={() => setFundiendo(null)}
        />
      )}
    </div>
  )
}
