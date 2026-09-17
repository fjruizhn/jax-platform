// Código estable de un error del backend (2026-09-14). El backend manda
// `detail` como texto (`config_clave_reservada`) o como objeto con `code` y
// datos extra (AdminSmtp: el código y lo que respondió el servidor SMTP).
// Cada pantalla traduce el código con su propio texto de i18n. Antes vivía
// solo dentro de AdminSmtp; lo comparten las pantallas de admin.
export function codigoDe(err) {
  const detail = err?.response?.data?.detail
  return typeof detail === 'string' ? detail : detail?.code
}

// 409 `modelo_sin_contrato_de_dispatch` (2026-09-14, PR-J): lo devuelven los
// dos escritores de facet_binding (aprobar una propuesta, PUT de un binding)
// cuando el modelo destino no declara lo que el dispatch de la faceta exige.
// Lo comparten AdminModelCatalog y AdminFacetBindings. Devuelve el texto
// traducido, o null si el error es otro (cada pantalla pone su genérico).
// Ronda 1: `modelo_de_otro_proveedor`, el mismo guard cuando el binding
// quedaría con un provider_id distinto al del modelo.
export function textoDeErrorDeBinding(t, err) {
  return textoDeDetalleDeBinding(t, err?.response?.data?.detail)
}

// Lo mismo desde el `detail` solo (PR-L, 2026-09-14): el último rechazo que
// GET /admin/models/proposals devuelve en `ultimo_rechazo` (guardado en
// model_catalog_audit) se lee con el mismo texto que el 409 en vivo.
export function textoDeDetalleDeBinding(t, detail) {
  if (detail?.code === 'modelo_sin_contrato_de_dispatch') {
    return t.modelo_sin_contrato_de_dispatch(detail.model_id, (detail.campos || []).join(', '))
  }
  if (detail?.code === 'modelo_de_otro_proveedor') {
    return t.modelo_de_otro_proveedor(detail.model_id, detail.provider_modelo, detail.provider_binding)
  }
  return null
}

// Errores de la Mesa (frente A, A-51, 2026-09-16): chat, comando, imagen,
// pipelines y subida responden un código estable. Nunca se muestra el código
// crudo ni un texto del backend; `motivo` (lo que dijo un servicio externo,
// ya redactado por el backend) se agrega como dato, igual que smtpServerSaid.
export function textoDeErrorDeMesa(t, err, generico) {
  return textoDeDetalleDeMesa(t, err?.response?.data?.detail, generico)
}

// Lo mismo desde el `detail` solo (Task 10, 2026-09-17): el `motivo` de
// POST /pipelines/{id}/continue/preflight es un detail `{code, ...}` que llega
// en un 200, sin respuesta de error alrededor.
export function textoDeDetalleDeMesa(t, detail, generico) {
  const code = typeof detail === 'string' ? detail : detail?.code
  // Object.hasOwn (ronda final M4): un código `constructor` o `toString` no es
  // una clave del diccionario, es una propiedad heredada de Object.
  const traducir = typeof code === 'string' && Object.hasOwn(t.erroresMesa, code) && t.erroresMesa[code]
  if (!traducir) return generico
  const datos = detail && typeof detail === 'object' ? detail : {}
  const partes = [traducir(datos)]
  // Sólo un string no vacío va como dato (fix round 2): un objeto sería
  // "[object Object]" en pantalla.
  if (typeof datos.motivo === 'string' && datos.motivo) partes.push(t.respuestaDelServicio(datos.motivo))
  // `mensaje` (estado_no_continuable, spec 2026-09-17): texto de Jacobs ya
  // redactado por el backend; va como dato, igual que `motivo`. Fix round 1:
  // si el status es uno que la Mesa sabe nombrar, el texto propio alcanza y
  // el mensaje de Jacobs no se agrega.
  const statusConocido = code === 'estado_no_continuable' && typeof datos.status === 'string'
    && Object.hasOwn(t.pipelineStatusLabels, datos.status)
  if (typeof datos.mensaje === 'string' && datos.mensaje && !statusConocido) partes.push(t.respuestaDelServicio(datos.mensaje))
  const detalle = textoDeDetalle(t, datos.detalle)
  if (detalle) partes.push(t.detalleDelPrevuelo(detalle))
  return partes.join(' ')
}

// `detalle` de un rechazo del pre-vuelo (adenda Task 8 ítem 3): texto, o lista
// normalizada [{paso, faceta|null, motivo}] (reasignacion_invalida,
// plan_rechazado). Cualquier otra forma no se muestra: nunca un objeto crudo.
function textoDeDetalle(t, detalle) {
  if (typeof detalle === 'string') return detalle || null
  if (!Array.isArray(detalle)) return null
  const items = detalle
    .filter((d) => d && typeof d === 'object')
    .map((d) => t.detalleDePaso(d))
  return items.length ? items.join('; ') : null
}

// Respuestas enlatadas del chat (A-53): `aviso` con código y params.
export function textoDeAviso(t, aviso) {
  const code = aviso?.code
  if (typeof code !== 'string' || !Object.hasOwn(t.avisosChat, code)) return t.avisoDesconocido
  const traducir = t.avisosChat[code]
  const params = aviso.params || {}
  if (code === 'identidad_del_modelo') {
    const hosting = typeof params.provider === 'string' && Object.hasOwn(t.hostingDeProveedor, params.provider)
    return traducir(params, hosting ? t.hostingDeProveedor[params.provider] : t.hostingGenerico)
  }
  return traducir(params)
}

// Violación del pre-vuelo (spec 2026-09-17 §4.1): se lee por su `regla`; una
// regla que esta versión no conoce cae en un texto genérico, nunca cruda. El
// `detalle` (redactado por el backend) va como dato.
export function textoDeViolacion(t, v) {
  const conocida = typeof v?.regla === 'string' && Object.hasOwn(t.reglasPrevuelo, v.regla)
  const base = (conocida ? t.reglasPrevuelo[v.regla] : t.reglaPrevueloDesconocida)(v || {})
  return typeof v?.detalle === 'string' && v.detalle ? `${base} ${t.detalleDelPrevuelo(v.detalle)}` : base
}

// Motivo por paso de `pasos_costo` (adenda Task 8 ítem 6): por qué el costo de
// ese paso es el que es. Desconocido o null → texto genérico, nunca crudo.
export function textoDeMotivoDeCosto(t, motivo) {
  if (typeof motivo === 'string' && Object.hasOwn(t.motivosDeCosto, motivo)) return t.motivosDeCosto[motivo]
  return t.motivoDeCostoDesconocido
}

// Causa del aborto de un pipeline (GET /api/pipelines → `causa`, spec
// 2026-09-17): se lee por su `tipo`; uno desconocido o heredado cae en
// `desconocida`. Sólo un `paso` entero llega al texto.
export function textoDeCausa(t, causa) {
  const tipo = causa && typeof causa === 'object' ? causa.tipo : undefined
  const conocida = typeof tipo === 'string' && Object.hasOwn(t.causasDeAborto, tipo)
  const datos = Number.isInteger(causa?.paso) ? { paso: causa.paso } : {}
  const base = (conocida ? t.causasDeAborto[tipo] : t.causasDeAborto.desconocida)(datos)
  // `detalle` (Task 10): el error del paso, ya redactado por el backend; va
  // como dato del servicio y sólo si es un string no vacío.
  return typeof causa?.detalle === 'string' && causa.detalle ? `${base} ${t.respuestaDelServicio(causa.detalle)}` : base
}

// Rechazo de crear o continuar un pipeline (Task 9, compartido desde la Task
// 10 por PipelineModal y ContinuarPipelineModal). Los rechazos se quedan
// dentro del modal:
// - prevuelo_rechazado con violaciones -> {tipo: 'violaciones'}.
// - confirmacion_de_costo / costo_supera_lo_aceptado con un costo legible ->
//   {tipo: 'costo'}: se vuelve a pedir la confirmación con el costo que
//   devolvió el backend (`previo` = el veredicto que se había confirmado, para
//   lo que el rechazo no traiga). `aviso` sólo si el costo subió.
// - cualquier otro -> {tipo: 'error', texto} traducido, o `generico`.
export function clasificarRechazo(t, err, previo, generico) {
  const code = codigoDe(err)
  const detail = err?.response?.data?.detail
  const datos = detail && typeof detail === 'object' ? detail : {}
  if (code === 'prevuelo_rechazado' && Array.isArray(datos.violaciones)) {
    return { tipo: 'violaciones', violaciones: datos.violaciones }
  }
  const esDeCosto = code === 'confirmacion_de_costo' || code === 'costo_supera_lo_aceptado'
  if (esDeCosto && typeof datos.costo_max_usd === 'string' && datos.costo_max_usd) {
    return {
      tipo: 'costo',
      veredicto: {
        costo_max_usd: datos.costo_max_usd,
        pasos_costo: Array.isArray(datos.pasos_costo) ? datos.pasos_costo : (previo?.pasos_costo || []),
        umbral_usd: datos.umbral_usd ?? previo?.umbral_usd ?? null,
      },
      aviso: code === 'costo_supera_lo_aceptado' ? textoDeErrorDeMesa(t, err, generico) : null,
    }
  }
  return { tipo: 'error', texto: textoDeErrorDeMesa(t, err, generico) }
}
