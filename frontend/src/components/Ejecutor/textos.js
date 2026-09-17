// Traducción de códigos del Ejecutor (SP2, 2026-09-17). Regla del contrato: un
// código que el diccionario no conoce se muestra CRUDO, nunca se esconde.
// Object.hasOwn: `constructor` o `toString` no son códigos del diccionario.

export function traducir(diccionario, codigo) {
  if (codigo === null || codigo === undefined || codigo === '') return ''
  const texto = typeof codigo === 'string' && diccionario && Object.hasOwn(diccionario, codigo) ? diccionario[codigo] : null
  return typeof texto === 'string' ? texto : String(codigo)
}

// Motivo de la pausa: los motivos reales de la pausa; si no, como código suelto.
export function traducirMotivoPausa(tx, codigo) {
  if (typeof codigo === 'string' && Object.hasOwn(tx.motivosPausa, codigo)) return tx.motivosPausa[codigo]
  return traducirCodigo(tx, codigo)
}

// Códigos sueltos: primero los de cita/auditor/arranque, después los del
// turno; si no, crudo.
export function traducirCodigo(tx, codigo) {
  if (typeof codigo === 'string' && Object.hasOwn(tx.codigos, codigo)) return tx.codigos[codigo]
  return traducir(tx.codigosTurno, codigo)
}

// Un valor de `datos` para mostrar: texto tal cual, lo demás como JSON.
export function valorLegible(v) {
  if (typeof v === 'string') return v
  try {
    return JSON.stringify(v)
  } catch {
    return String(v)
  }
}

export function datosLegibles(datos) {
  if (!datos || typeof datos !== 'object') return ''
  return Object.entries(datos).map(([k, v]) => `${k}: ${valorLegible(v)}`).join(' · ')
}

// Error de un pedido al Ejecutor → texto visible. `detail` es un código o un
// objeto {codigo, ...datos}. Nunca devuelve «[object Object]».
export function textoDeErrorEjecutor(t, err) {
  const tx = t.ejecutor
  const respuesta = err?.response
  if (!respuesta) return tx.errorDeRed
  const detail = respuesta.data?.detail
  const esObjeto = detail !== null && typeof detail === 'object'
  const codigo = typeof detail === 'string' ? detail : esObjeto ? detail.codigo : undefined
  if (typeof codigo !== 'string' || codigo === '') return tx.errorHttp(respuesta.status ?? '—')
  const { codigo: _c, ...datos } = esObjeto ? detail : {}
  const traductor = Object.hasOwn(tx.errores, codigo) ? tx.errores[codigo] : null
  if (typeof traductor === 'function') {
    return traductor({ ...datos, motivo: traducir(tx.motivosNoElegible, datos.motivo) })
  }
  const extra = datosLegibles(datos)
  return extra ? `${codigo} · ${extra}` : codigo
}

// Código dentro de los `datos` de un evento de bitácora: turno → cita,
// descartadas y auditor → motivos de pausa → crudo.
const CLAVES_CON_CODIGO = new Set(['codigo', 'motivo', 'estado'])

export function traducirCodigoDeBitacora(tx, codigo) {
  for (const dic of [tx.codigosTurno, tx.codigos, tx.motivosPausa]) {
    if (typeof codigo === 'string' && Object.hasOwn(dic, codigo)) return dic[codigo]
  }
  return valorLegible(codigo)
}

// `datos` de un evento, legible: codigo/motivo/estado traducidos y `fallos`
// (arranque_rechazado) como contrato + código, igual que en el detalle.
export function datosDeBitacora(tx, datos) {
  if (!datos || typeof datos !== 'object') return ''
  return Object.entries(datos).map(([k, v]) => {
    if (CLAVES_CON_CODIGO.has(k)) return `${k}: ${traducirCodigoDeBitacora(tx, v)}`
    if (k === 'fallos' && Array.isArray(v)) {
      const fallos = v.map((f) => (f && typeof f === 'object'
        ? [traducir(tx.contratos, f.contrato), traducirCodigo(tx, f.codigo)].filter(Boolean).join(' — ')
        : valorLegible(f)))
      return `${k}: ${fallos.join('; ')}`
    }
    return `${k}: ${valorLegible(v)}`
  }).join(' · ')
}
