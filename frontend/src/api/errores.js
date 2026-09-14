// Código estable de un error del backend (2026-09-14). El backend manda
// `detail` como texto (`config_clave_reservada`) o como objeto con `code` y
// datos extra (AdminSmtp: el código y lo que respondió el servidor SMTP).
// Cada pantalla traduce el código con su propio texto de i18n. Antes vivía
// solo dentro de AdminSmtp; lo comparten las pantallas de admin.
export function codigoDe(err) {
  const detail = err?.response?.data?.detail
  return typeof detail === 'string' ? detail : detail?.code
}
