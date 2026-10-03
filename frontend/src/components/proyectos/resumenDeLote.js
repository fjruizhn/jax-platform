// Resumen previo a la subida (E2a, T9). Función pura: decide, ANTES de subir,
// lo mismo que el servidor decidirá después, para que la persona vea qué entra
// y qué se ignora y por qué. El servidor sigue siendo la autoridad: esto no lo
// reemplaza, solo evita subir lo que ya se sabe que va a rechazar.
//
// Los topes y las extensiones NO viven aquí: llegan de
// GET /proyectos/documentos/limites. Sin límites cargados no se inventa ningún
// tope: nada es admitido (falla cerrado).
//
// Orden de las reglas (el mismo del backend): tipo, tamaño por archivo,
// duplicado dentro del lote. Los topes del lote se miden solo sobre lo
// aceptado: lo ignorado no se sube y no pesa.

// En una carpeta el nombre es la ruta relativa (igual que subirDocumentos).
const nombreDe = (f) => f.webkitRelativePath || f.name

function extensionDe(nombre) {
  const i = nombre.lastIndexOf('.')
  if (i <= 0 || i === nombre.length - 1) return null
  return nombre.slice(i + 1).toLowerCase()
}

export function resumirLote(files, limites) {
  // Un tope ausente o no numérico no deja pasar todo: sin los tres topes y la
  // lista de tipos, nada se admite (falla cerrado, igual que sin límites).
  const completos = ['max_bytes_archivo', 'max_archivos_lote', 'max_bytes_lote']
    .every((k) => Number.isFinite(limites?.[k]))
  const admitidas = new Set(completos ? (limites.extensiones ?? []) : [])
  const aceptados = []
  const ignorados = []
  const porTipo = {}
  const vistos = new Set()
  let totalBytes = 0

  for (const f of files) {
    const nombre = nombreDe(f)
    const ext = extensionDe(f.name)
    if (!ext || !admitidas.has(ext)) {
      ignorados.push({ nombre, motivo: 'tipo_no_admitido' })
      continue
    }
    if (f.size > limites.max_bytes_archivo) {
      ignorados.push({ nombre, motivo: 'demasiado_grande' })
      continue
    }
    const clave = `${nombre}\u0000${f.size}`
    if (vistos.has(clave)) {
      ignorados.push({ nombre, motivo: 'duplicado' })
      continue
    }
    vistos.add(clave)
    aceptados.push(f)
    porTipo[ext] = (porTipo[ext] ?? 0) + 1
    totalBytes += f.size
  }

  let excedeLote = null
  if (aceptados.length > (limites?.max_archivos_lote ?? 0)) excedeLote = 'archivos'
  else if (totalBytes > (limites?.max_bytes_lote ?? 0)) excedeLote = 'bytes'
  return { aceptados, ignorados, totalBytes, porTipo, excedeLote }
}

const CLAVES = ['b', 'kb', 'mb', 'gb']

// Base BINARIA (1024), como el sistema operativo. El número lo formatea Intl
// decimal (sin `style:'unit'`, que lanza RangeError en navegadores sin ese
// soporte) y la unidad viene del i18n (`proyectos.documentos.unidades`).
export function formatoPeso(bytes, locale, unidades) {
  let valor = bytes
  let u = 0
  while (valor >= 1024 && u < CLAVES.length - 1) { valor /= 1024; u += 1 }
  const num = new Intl.NumberFormat(locale, { maximumFractionDigits: u === 0 ? 0 : 1 }).format(valor)
  return `${num} ${unidades[CLAVES[u]]}`
}
