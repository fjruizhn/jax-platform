import { readFileSync } from 'node:fs'

// Lee y valida el archivo VERSION (misma regla que backend/app_version.py:
// X.Y o X.Y.Z). Vacío, con otro formato o inexistente: lanza con un mensaje
// claro, así el build y el dev fallan en vez de compilar una versión inventada.
export function leerVersion(ruta) {
  let crudo
  try {
    crudo = readFileSync(ruta, 'utf8')
  } catch (e) {
    throw new Error(`No se pudo leer el archivo VERSION (${ruta}): ${e.message}`)
  }
  const version = crudo.trim()
  if (!/^\d+\.\d+(\.\d+)?$/.test(version)) {
    throw new Error(`El archivo VERSION (${ruta}) debe contener X.Y o X.Y.Z; hay ${JSON.stringify(version)}`)
  }
  return version
}
