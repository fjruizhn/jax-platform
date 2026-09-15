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
export function textoDeErrorDeBinding(t, err) {
  if (codigoDe(err) !== 'modelo_sin_contrato_de_dispatch') return null
  const detail = err.response.data.detail
  return t.modelo_sin_contrato_de_dispatch(detail.model_id, (detail.campos || []).join(', '))
}
