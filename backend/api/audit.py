import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException

from auth.middleware import require_superadmin
from auth.models import AuthUser
from config_de_entorno import ruta_requerida

router = APIRouter(prefix="/api")

AUDIT_LOG = ruta_requerida("JAX_AUDIT_LOG_PATH")


_BLOQUE = 64 * 1024


def _ultimas_20_lineas(ruta, n: int = 20) -> list[str]:
    """Las ultimas `n` lineas no vacias, en orden de archivo.

    Task 15 R12(a) (2026-09-16): lee desde el FINAL en bloques hacia atras. La
    version anterior (A-41) recorria el archivo entero linea a linea: con un
    audit de 50 MB y 10 lectores, los hilos le disputaban el GIL al loop y el
    p95 de /api/health subia 15-19x. Ahora el costo es O(cola).

    Solo se decodifican lineas COMPLETAS (un bloque puede partir un caracter
    multibyte); un utf-8 invalido en la cola sigue lanzando UnicodeDecodeError.
    Las vacias no cuentan entre las `n`. Un archivo ausente es `[]` aca, en el
    hilo: antes un `exists()` en el loop era un stat bloqueante y una carrera.
    """
    try:
        archivo = open(ruta, "rb")
    except FileNotFoundError:
        return []
    halladas: list[str] = []  # de la mas nueva a la mas vieja

    def agregar(crudo: bytes) -> None:
        linea = crudo.decode("utf-8")
        if linea.strip():
            halladas.append(linea)

    with archivo:
        fin = archivo.seek(0, 2)
        pendiente: list[bytes] = []  # trozos de la linea parcial del frente, del mas nuevo al mas viejo
        while fin > 0 and len(halladas) < n:
            paso = min(_BLOQUE, fin)
            fin -= paso
            archivo.seek(fin)
            trozo = archivo.read(paso)
            pendiente.append(trozo)
            if b"\n" not in trozo:
                continue  # linea mas larga que un bloque: se junta una sola vez, al hallar su inicio
            partes = b"".join(reversed(pendiente)).split(b"\n")
            pendiente = [partes[0]]
            for crudo in reversed(partes[1:]):
                if len(halladas) >= n:
                    break
                agregar(crudo)
        if fin == 0 and len(halladas) < n and pendiente:
            agregar(b"".join(reversed(pendiente)))
    return halladas[:n][::-1]


# Task 6 S3 (2026-09-15): el log forense de LAS MANOS (hosts, capacidades,
# el motivo de cada rechazo de politica y stdout/stderr de lo que ejecutan
# las facetas) solo exigia sesion; un viewer lo leia entero. Solo superadmin.
@router.get("/audit")
async def get_audit(user: AuthUser = Depends(require_superadmin)):
    try:
        lineas = await asyncio.to_thread(_ultimas_20_lineas, AUDIT_LOG)
    except (OSError, UnicodeDecodeError) as exc:
        # Task 3 (2026-09-15, clase b): antes `except Exception` devolvia
        # {"events": []} -- un audit ilegible se veia igual que "no hubo
        # eventos". Un fallo del camino de auditoria no se disfraza de sano.
        raise HTTPException(status_code=503, detail="auditoria_ilegible") from exc
    events = []
    for line in reversed(lineas):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:  # fail-soft: descarta una linea JSONL corrupta entre las ultimas 20 mostradas; el resto del log se muestra igual, no es un fallo total silencioso
            pass
    return {"events": events}
