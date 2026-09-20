import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useI18n } from '../i18n/index.jsx'
import { useNombreDelSistema } from '../store/useApariencia'
import { useJaxStore } from '../store/useJaxStore'
import api from '../api/client'
import { codigoDe } from '../api/errores'
import Dialogo from '../components/Dialogo'
import ConfirmacionSuma from '../components/ConfirmacionSuma'
import Toast from '../components/Notifications/Toast'
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
const emptySet = () => new Set()

function mensajeDeError(t, err) {
  const code = codigoDe(err)
  return (code && t.memoria.errores[code]) || t.memoria.errorGenerico
}

export default function Memoria() {
  const { t } = useI18n()
  const nombre = useNombreDelSistema(t)
  const addToast = useJaxStore((s) => s.addToast)
  const usuario = useJaxStore((s) => s.user)

  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState(false)
  const [grupos, setGrupos] = useState([])
  const [hechosPorId, setHechosPorId] = useState({})
  const [vencidos, setVencidos] = useState([])
  const [seleccionados, setSeleccionados] = useState(emptySet)
  const [procesando, setProcesando] = useState(emptySet)

  // Ventanas propias (Global Constraints: nunca los diálogos del navegador).
  // Sólo una a la vez, mismo patrón que AdminUsers/AdminSmtp.
  const [corrigiendo, setCorrigiendo] = useState(null) // { hecho, texto, err }
  const [confirmandoCorreccion, setConfirmandoCorreccion] = useState(null) // { hecho, texto }
  const [caducando, setCaducando] = useState(null) // hecho
  const [fundiendo, setFundiendo] = useState(null) // ids[]

  const cargar = useCallback(async () => {
    setCargando(true)
    setError(false)
    try {
      const [rGrupos, rHechos, rVencidos] = await Promise.all([
        api.get('/admin/memoria/grupos'),
        api.get('/admin/memoria/hechos', { params: { limite: 500 } }),
        api.get('/admin/memoria/hechos', { params: { limite: 500, incluir_vencidos: true } }),
      ])
      const porId = {}
      for (const h of rHechos.data.hechos) porId[h.id] = h
      // No se preseleccionan los casi-duplicados: ver nota de módulo, punto 3.
      const idsDeCluster = new Set(rGrupos.data.grupos.flatMap((g) => (g.casi_duplicados || []).flat()))
      setGrupos(rGrupos.data.grupos)
      setHechosPorId(porId)
      // incluir_vencidos=true trae vencidos Y activos juntos (ver nota de
      // módulo): acá se filtra sólo lo vencido, para la sección aparte.
      setVencidos(rVencidos.data.hechos.filter((h) => h.vencido))
      setSeleccionados(new Set(
        rHechos.data.hechos.filter((h) => !h.verificado && !idsDeCluster.has(h.id)).map((h) => h.id),
      ))
    } catch {
      setError(true)
    } finally {
      setCargando(false)
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

  function actualizarHecho(id, cambios) {
    setHechosPorId((prev) => (prev[id] ? { ...prev, [id]: { ...prev[id], ...cambios } } : prev))
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
  async function aprobar(ids) {
    if (!ids.length) return
    marcarProcesando(ids, true)
    try {
      const { data } = await api.post('/admin/memoria/hechos/aprobar', { ids })
      const ahora = new Date().toISOString()
      for (const id of ids) actualizarHecho(id, { verificado: true, verificado_por: usuario?.user_id ?? null, verificado_at: ahora })
      onSeleccionarNinguno(ids)
      addToast({ type: 'success', message: t.memoria.aprobados(data.aprobados ?? ids.length) })
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
      actualizarHecho(hecho.id, { vencido: true, vence_at })
      setCaducando(null)
      addToast({ type: 'success', message: t.memoria.caducado })
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
      actualizarHecho(id, { vencido: false, vence_at: null })
      setVencidos((prev) => prev.filter((h) => h.id !== id))
      addToast({ type: 'success', message: t.memoria.caducidadQuitada })
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

  // Fundir (casi-duplicados, spec §2.1: "un botón para fundirlos"): aprueba
  // el más reciente del cluster y SUPERA el resto (POST /hechos/fundir --
  // ver nota de módulo). Sigue siendo destructivo (cambia el estado de
  // varios hechos a la vez, sin vuelta atrás desde acá) -> ConfirmacionSuma.
  function abrirFundir(ids) {
    setFundiendo(ids)
  }

  async function confirmarFundir() {
    // item.ids viene ordenado created_at DESC (GrupoDeHechos.jsx): el
    // primero es el más reciente.
    const [masReciente, ...resto] = fundiendo
    marcarProcesando(fundiendo, true)
    try {
      await api.post('/admin/memoria/hechos/aprobar', { ids: [masReciente] })
      await api.post('/admin/memoria/hechos/fundir', {
        superviviente_id: masReciente, absorbidos: resto,
      })
      setFundiendo(null)
      addToast({ type: 'success', message: t.memoria.fundido })
      await cargar() // cambio estructural (superseded_by en varios hechos): recargar, igual que corregir.
    } catch (err) {
      addToast({ type: 'error', message: mensajeDeError(t, err) })
    } finally {
      marcarProcesando(fundiendo, false)
    }
  }

  const totalSinVerificar = Object.values(hechosPorId).filter((h) => !h.verificado).length

  return (
    <div className="min-h-dvh bg-fondo text-texto p-6">
      <div className="max-w-4xl mx-auto space-y-4">
        <div className="flex items-center justify-between mb-2">
          <div>
            <h1 className="text-xl font-bold text-texto-fuerte">{t.memoria.titulo}</h1>
            <p className="text-xs text-texto-tenue mt-0.5">{t.memoria.subtitulo(nombre)}</p>
          </div>
          <Link to="/" className="text-xs text-texto-tenue hover:text-texto transition-colors flex items-center gap-1.5 flex-shrink-0">
            <span>←</span>
            <span>{t.memoria.volver(nombre)}</span>
          </Link>
        </div>

        {!cargando && !error && (
          <p className="text-xs text-texto-suave">{t.memoria.totalSinVerificar(totalSinVerificar)}</p>
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
          <SeccionVencidos vencidos={vencidos} procesando={procesando} onQuitarCaducidad={quitarCaducidad} />
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
          titulo={t.memoria.fundirTitulo}
          mensaje={t.memoria.fundirMensaje}
          textoConfirmar={t.memoria.fundirConfirmar}
          onConfirmar={confirmarFundir}
          onCancelar={() => setFundiendo(null)}
        />
      )}

      <Toast />
    </div>
  )
}
