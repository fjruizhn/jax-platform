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
  const code = codigoDe(err)
  // Object.hasOwn (ronda final M4): un código `constructor` o `toString` no es
  // una clave del diccionario, es una propiedad heredada de Object.
  const traducir = typeof code === 'string' && Object.hasOwn(t.erroresMesa, code) && t.erroresMesa[code]
  if (!traducir) return generico
  const detail = err?.response?.data?.detail
  const datos = detail && typeof detail === 'object' ? detail : {}
  const base = traducir(datos)
  return datos.motivo ? `${base} ${t.respuestaDelServicio(datos.motivo)}` : base
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
