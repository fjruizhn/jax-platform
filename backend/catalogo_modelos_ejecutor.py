"""Ejecutor programado del catálogo de modelos (2026-09-27).

`POST /api/admin/models/sync` (api/admin/models.py) sólo se dispara con el
click de un superadmin -- no hay nada programado. Desde que los servicios
corren como `jaxsvc` (17-sep, HOME=/var/lib/jaxsvc) el proveedor `anthropic`
se saltaba en SILENCIO en cada corrida (sin `~/.claude/.credentials.json`
de ese usuario) y la respuesta seguía diciendo `ok: true`, porque antes sólo
`error` bajaba `ok`, nunca `skipped`: Opus 5.5 nunca entró al catálogo y
nadie se enteró porque nadie estaba mirando la pantalla.

Este módulo:

1. Corre `model_catalog.sync_all()` -- la MISMA lógica que usa el endpoint
   (Regla Absoluta: una sola fuente de "qué significa que el catálogo esté
   sano", nunca dos implementaciones que puedan divergir).
2. Imprime un resumen de una línea por stdout (la unidad systemd lo manda al
   journal).
3. Sale con código != 0 si `ok` es falso -- lo que
   `jax-catalogo-modelos.service` usa para marcar el job en rojo.
4. Avisa por Telegram, reusando `jacobs.reaper.send_telegram_alert` del repo
   `jax` -- jax-platform NO tiene su propio cliente de Telegram a propósito
   (ver `aviso_pipeline.py`: "El de Telegram vive en el otro repo (jax)").
   Nunca repite el mismo aviso más de una vez por ventana de 24 h, y un
   aviso roto (Telegram caído, disco lleno) nunca enmascara el código de
   salida del job.

Uso: `python -m catalogo_modelos_ejecutor` desde `backend/`, con el mismo
entorno que el resto del servicio (`/etc/jax/.env`, `JAX_REPO_PATH`).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path

import model_catalog
from db.connection import close_pool

logger = logging.getLogger("catalogo_modelos_ejecutor")

#: Directorio donde se guarda el estado del último aviso (dedupe). Variable
#: de entorno primero; sin ella, bajo el HOME del proceso -- en producción
#: eso es jaxsvc (/var/lib/jaxsvc), el mismo usuario que corre el resto del
#: servicio.
ESTADO_DIR_ENV = "JAX_CATALOGO_ESTADO_DIR"

#: No se reavisa del MISMO conjunto de problemas antes de que pase esto --
#: evita el spam de "sigue roto" cada 6 h (el timer corre cada 6 h,
#: ver ops/); un problema NUEVO, en cambio, avisa de inmediato.
VENTANA_REAVISO_SEGUNDOS = 24 * 60 * 60


def _estado_dir() -> Path:
    configurado = os.environ.get(ESTADO_DIR_ENV, "").strip()
    if configurado:
        return Path(configurado)
    return Path.home() / ".jax-catalogo-modelos"


def _ruta_estado() -> Path:
    return _estado_dir() / "ultimo-aviso.json"


def _firma(objeto) -> str:
    """Hash estable de cualquier estructura JSON-serializable -- dos
    corridas con el MISMO conjunto de problemas (aunque en otro orden
    interno) dan la misma firma. `sort_keys` hace el orden de las claves
    irrelevante; las LISTAS ya llegan ordenadas por quien arma `objeto`
    (ver `_problemas_de`/`_nuevos_de`), para que el orden de una lista
    tampoco cambie la firma."""
    crudo = json.dumps(objeto, sort_keys=True, default=str)
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


def _cargar_estado(ruta: Path) -> dict:
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _guardar_estado(ruta: Path, estado: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado, sort_keys=True), encoding="utf-8")
    tmp.replace(ruta)


def _debe_avisar(entrada: dict | None, firma_actual: str, ahora: float) -> bool:
    """¿Toca mandar el aviso? Sí si es la primera vez, si el conjunto de
    problemas cambió, o si ya pasó la ventana de reaviso -- nunca por "ya se
    avisó antes y nada cambió", que es justo el spam que esto evita."""
    if not entrada:
        return True
    if entrada.get("firma") != firma_actual:
        return True
    notificado_en = entrada.get("notificado_en")
    if not isinstance(notificado_en, (int, float)):
        return True
    return (ahora - notificado_en) >= VENTANA_REAVISO_SEGUNDOS


def _problemas_de(resultado: dict) -> dict | None:
    """Subconjunto ESTABLE de `resultado` que define "hay un problema" --
    nunca timestamps ni conteos que cambian sin que el problema cambie
    (fetched, source_checked_at): eso rompería el dedupe en cada corrida."""
    if resultado["ok"]:
        return None
    return {
        "providers_fallidos": sorted(resultado.get("providers_fallidos") or []),
        "providers_saltados": sorted(resultado.get("providers_saltados") or []),
        "enrich_fallido": bool(resultado.get("enrich_fallido")),
        "facetas_en_riesgo": sorted(
            f"{f['facet_key']}:{f.get('provider_id')}:{f.get('model_id')}:{f.get('status')}"
            for f in resultado.get("facetas_en_riesgo") or []
        ),
    }


def _nuevos_de(resultado: dict) -> dict | None:
    nuevos = {p: sorted(ids) for p, ids in (resultado.get("nuevos") or {}).items() if ids}
    return nuevos or None


def _mensaje_problemas(resultado: dict) -> str:
    partes = ["Catálogo de modelos: hay problemas."]
    if resultado.get("providers_fallidos"):
        partes.append(f"Proveedores con error: {', '.join(resultado['providers_fallidos'])}.")
    if resultado.get("providers_saltados"):
        partes.append(f"Proveedores saltados: {', '.join(resultado['providers_saltados'])}.")
    if resultado.get("enrich_fallido"):
        partes.append("El enriquecimiento (models.dev) falló.")
    if resultado.get("facetas_en_riesgo"):
        riesgos = ", ".join(
            f"{f['facet_key']}->{f.get('provider_id')}/{f.get('model_id')} ({f.get('status')})"
            for f in resultado["facetas_en_riesgo"]
        )
        partes.append(f"Facetas en riesgo: {riesgos}.")
    return " ".join(partes)


def _mensaje_nuevos(nuevos: dict) -> str:
    lineas = ", ".join(f"{proveedor}: {', '.join(ids)}" for proveedor, ids in sorted(nuevos.items()))
    return f"Catálogo de modelos: modelos nuevos detectados -- {lineas}."


def _enviar_telegram(mensaje: str) -> None:
    """Best-effort: NUNCA levanta. Reusa el ÚNICO cliente de Telegram del
    ecosistema (jax/jacobs/reaper.py::send_telegram_alert) -- jax-platform
    no tiene el suyo a propósito (ver aviso_pipeline.py). Mismo patrón de
    sys.path que governance_context.py: JAX_REPO_PATH validado con
    `config_entorno.ruta_absoluta_requerida`, nunca un import de un paquete
    llamado 'jax' preinstalado sin relación."""
    try:
        from config_entorno import ruta_absoluta_requerida

        repo = ruta_absoluta_requerida("JAX_REPO_PATH")
        for ruta in (str(repo), str(repo / "las_manos")):
            if ruta not in sys.path:
                sys.path.insert(0, ruta)
        from jacobs.reaper import send_telegram_alert

        resultado = asyncio.run(send_telegram_alert(mensaje))
        if not resultado.get("ok"):
            logger.warning(
                f"catalogo_modelos_ejecutor: aviso Telegram no confirmado: {resultado.get('error')}"
            )
    except Exception:  # fail-soft: un aviso roto no puede tumbar el job ni enmascarar su código de salida
        logger.exception("catalogo_modelos_ejecutor: fallo enviando aviso a Telegram")


def _avisar(resultado: dict) -> None:
    ruta = _ruta_estado()
    estado = _cargar_estado(ruta)
    ahora = time.time()
    cambio = False

    problemas = _problemas_de(resultado)
    if problemas is None:
        # Catálogo sano: no hay nada que avisar, y se limpia el "ya avisado"
        # de la vez pasada -- si el MISMO problema reaparece más adelante,
        # tiene que volver a avisar (no seguir suprimido por una corrida
        # sana intermedia).
        if "problemas" in estado:
            del estado["problemas"]
            cambio = True
    else:
        firma = _firma(problemas)
        if _debe_avisar(estado.get("problemas"), firma, ahora):
            _enviar_telegram(_mensaje_problemas(resultado))
            estado["problemas"] = {"firma": firma, "notificado_en": ahora}
            cambio = True

    nuevos = _nuevos_de(resultado)
    if nuevos is None:
        if "nuevos" in estado:
            del estado["nuevos"]
            cambio = True
    else:
        firma = _firma(nuevos)
        if _debe_avisar(estado.get("nuevos"), firma, ahora):
            _enviar_telegram(_mensaje_nuevos(nuevos))
            estado["nuevos"] = {"firma": firma, "notificado_en": ahora}
            cambio = True

    if cambio:
        _guardar_estado(ruta, estado)


def _resumen(resultado: dict) -> str:
    conteo_nuevos = {p: len(ids) for p, ids in (resultado.get("nuevos") or {}).items()}
    facetas = [f["facet_key"] for f in resultado.get("facetas_en_riesgo") or []]
    return (
        f"catalogo_modelos ok={resultado['ok']} "
        f"providers_fallidos={resultado.get('providers_fallidos')} "
        f"providers_saltados={resultado.get('providers_saltados')} "
        f"enrich_fallido={resultado.get('enrich_fallido')} "
        f"nuevos={conteo_nuevos} "
        f"facetas_en_riesgo={facetas}"
    )


async def _correr() -> dict:
    try:
        return await model_catalog.sync_all()
    finally:
        # Este proceso termina apenas main() retorna -- cerrar el pool acá
        # (mismo loop que lo creó, ver db/connection.py) evita que la
        # interpretación cierre con conexiones a medio cerrar.
        await close_pool()


def main() -> int:
    resultado = asyncio.run(_correr())
    print(_resumen(resultado))
    try:
        _avisar(resultado)
    except Exception:  # fail-soft: el código de salida es sobre `ok`, nunca sobre si el aviso salió bien
        logger.exception("catalogo_modelos_ejecutor: _avisar falló, el código de salida no cambia por esto")
    return 0 if resultado["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
