"""Ejecutor programado del catálogo de modelos (2026-09-27, revisado el
mismo día tras la auditoría adversarial del commit d549335 -- A-1/A-2/A-5).

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
3. Sale con código != 0 si `ok` es falso, O si `sync_all()` reventó de una
   manera que ni su propio try/except interno cubre (A-2: DB caída al
   conectar, un import roto) -- antes ese segundo caso salía en rojo pero
   MUDO, sin avisar a nadie.
4. Avisa por Telegram con un envío PROPIO y mínimo (A-5: ya no se importa
   `jacobs.reaper.send_telegram_alert` del repo `jax` -- ver el docstring de
   `_enviar_telegram` para el motivo). El dedupe SÓLO avanza si Telegram
   confirmó la entrega (A-1): un aviso que falla no se marca como avisado,
   la corrida siguiente reintenta, y un modelo nuevo que no se pudo avisar
   se acumula como pendiente hasta que un envío salga -- nunca se pierde en
   silencio.
5. Un aviso roto (Telegram caído, disco lleno) nunca enmascara el código de
   salida del job.

Uso: `python -m catalogo_modelos_ejecutor` desde `backend/`, con el mismo
entorno que el resto del servicio (`/etc/jax/.env`) más
`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`. Ya NO depende de `JAX_REPO_PATH`
(A-5): el envío propio a Telegram no cruza a otro repo.
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
from http_client import get_http_client
from redaccion import redactar_secretos, texto_de_error

logger = logging.getLogger("catalogo_modelos_ejecutor")

#: Directorio donde se guarda el estado del último aviso (dedupe + pendientes
#: de "nuevos"). Variable de entorno primero; sin ella, bajo el HOME del
#: proceso -- en producción eso es jaxsvc (/var/lib/jaxsvc), el mismo
#: usuario que corre el resto del servicio.
ESTADO_DIR_ENV = "JAX_CATALOGO_ESTADO_DIR"

#: Credenciales del bot -- las MISMAS variables que ya usa `jax/jacobs/
#: reaper.py::send_telegram_alert` (mismo ecosistema, un solo bot), pero acá
#: se leen y se usan directo: sin importar ese módulo (A-5).
TELEGRAM_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID_ENV = "TELEGRAM_CHAT_ID"

#: No se reavisa del MISMO conjunto de "problemas" antes de que pase esto --
#: evita el spam de "sigue roto" cada 6 h (el timer corre cada 6 h, ver
#: ops/); un problema NUEVO, en cambio, avisa de inmediato. "nuevos" NO usa
#: esta ventana -- ver `_avisar`.
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
    (ver `_problemas_de`), para que el orden de una lista tampoco cambie la
    firma."""
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
    """¿Toca INTENTAR mandar el aviso de "problemas"? Sí si es la primera
    vez, si el conjunto de problemas cambió, o si ya pasó la ventana de
    reaviso -- nunca por "ya se avisó antes y nada cambió", que es justo el
    spam que esto evita. Sólo se usa para "problemas": "nuevos" se acumula
    como pendiente (ver `_avisar`), no se dedupea por ventana."""
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
    """Lo que ESTA corrida vio por primera vez para cada proveedor. No es
    "lo pendiente de avisar" -- eso vive en el archivo de estado y lo arma
    `_fusionar_nuevos`."""
    nuevos = {p: sorted(ids) for p, ids in (resultado.get("nuevos") or {}).items() if ids}
    return nuevos or None


def _fusionar_nuevos(pendientes: dict, detectados: dict) -> dict:
    """Unión por proveedor de lo pendiente de avisar (de una corrida
    anterior cuyo envío falló) con lo detectado en ESTA corrida.

    A-1 (auditoría adversarial, 2026-09-27): `sync_provider_models` ya
    insertó en `model` lo que ve como nuevo -- la corrida SIGUIENTE no lo
    va a reportar de nuevo en `resultado["nuevos"]`, sin importar si el
    aviso de HOY salió o no. Sin este acumulador, un modelo nuevo cuyo
    aviso falló se perdía para siempre en el siguiente `_avisar()`, sin que
    nadie se enterara jamás de que existió. Acá se fusiona con lo que ya
    estaba pendiente y sólo se limpia cuando un envío confirma la entrega."""
    proveedores = set(pendientes) | set(detectados)
    fusion = {
        p: sorted(set(pendientes.get(p, [])) | set(detectados.get(p, [])))
        for p in proveedores
    }
    return {p: ids for p, ids in fusion.items() if ids}


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


def _mensaje_fallo_critico(motivo: str) -> str:
    return f"Catálogo de modelos: el vigilante falló al correr -- {motivo}"


async def _enviar_telegram(mensaje: str) -> bool:
    """Envío propio y mínimo (A-1/A-5, auditoría adversarial del commit
    d549335, 2026-09-27). ANTES importaba `jacobs.reaper.send_telegram_alert`
    (repo `jax`) -- eso mete en `sys.path` un checkout entero de otro repo y
    arrastra `jacobs.store`/`jacobs.policy`/`interruptor`: módulos que
    pueden chocar con algo ya presente en `sys.modules` de ESTE proceso
    (jax-platform tiene módulos propios con nombres parecidos) -- el
    resultado es un import híbrido frágil, que depende del ORDEN en que algo
    se haya importado antes en este mismo proceso, no sólo de qué hay en
    disco. Este envío usa el cliente HTTP YA compartido de jax-platform
    (`http_client.get_http_client`) y no toca `jax` para nada.

    Devuelve True SÓLO si Telegram confirmó la entrega (200 + body['ok']) --
    nunca "se intentó mandar". `_avisar()` depende de este valor real para
    decidir si el dedupe avanza (A-1): antes se marcaba "avisado" aunque el
    envío fallara, y un Telegram caído dejaba el catálogo roto en silencio
    otras 24h sin que nadie insistiera.

    El token del bot NUNCA se loguea ni se devuelve: viaja en el PATH de la
    URL (`.../bot<token>/sendMessage`), forma que NINGUNA regla de
    `redactar_secretos` reconoce por defecto (no es un query param ni tiene
    la forma AIza...) -- se pasa `secretos=[token]` EXPLÍCITO, que tapa por
    substring exacto antes de cualquier patrón, tanto en el log de una
    excepción de red como en el cuerpo de una respuesta sin confirmar."""
    token = os.environ.get(TELEGRAM_TOKEN_ENV, "").strip()
    chat_id = os.environ.get(TELEGRAM_CHAT_ID_ENV, "").strip()
    if not token or not chat_id:
        logger.warning(
            f"catalogo_modelos_ejecutor: {TELEGRAM_TOKEN_ENV}/{TELEGRAM_CHAT_ID_ENV} "
            "no configurados, aviso suprimido"
        )
        return False

    try:
        client = await get_http_client()
        resp = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": mensaje},
            timeout=10.0,
        )
    except Exception as e:  # fail-soft: red caída/timeout no puede tumbar el job
        logger.warning(
            "catalogo_modelos_ejecutor: fallo de red enviando a Telegram: "
            f"{texto_de_error(e, secretos=[token])}"
        )
        return False

    try:
        cuerpo = resp.json()
    except Exception:  # fail-soft: una respuesta no-JSON tampoco puede tumbar el job
        logger.warning(
            f"catalogo_modelos_ejecutor: Telegram respondió algo no-JSON, status={resp.status_code}"
        )
        return False

    if resp.status_code == 200 and cuerpo.get("ok"):
        return True

    logger.warning(
        "catalogo_modelos_ejecutor: Telegram no confirmó la entrega "
        f"(status={resp.status_code} body={redactar_secretos(str(cuerpo), secretos=[token])})"
    )
    return False


async def _avisar(resultado: dict) -> None:
    ruta = _ruta_estado()
    estado = _cargar_estado(ruta)
    ahora = time.time()
    cambio = False

    # --- Problemas: dedupe por firma+ventana, pero el estado SÓLO avanza si
    # Telegram confirmó la entrega (A-1). Si falla, no se toca nada: la
    # firma actual sigue "sin avisar" y la corrida siguiente reintenta sola.
    problemas = _problemas_de(resultado)
    if problemas is None:
        # Catálogo sano: se limpia el "ya avisado" de la vez pasada -- si el
        # MISMO problema reaparece más adelante, tiene que volver a avisar.
        if "problemas" in estado:
            del estado["problemas"]
            cambio = True
    else:
        firma = _firma(problemas)
        if _debe_avisar(estado.get("problemas"), firma, ahora):
            if await _enviar_telegram(_mensaje_problemas(resultado)):
                estado["problemas"] = {"firma": firma, "notificado_en": ahora}
                cambio = True
            # si falla: no se escribe nada -- la firma actual sigue sin
            # figurar como avisada, así que la corrida siguiente reintenta.

    # --- Nuevos: acumulador PERSISTENTE, no firma+ventana (A-1, ver
    # `_fusionar_nuevos`). Se guarda lo pendiente ANTES de intentar el envío
    # (si el proceso muriera a mitad del POST, no se pierde lo detectado) y
    # sólo se limpia cuando Telegram confirma la entrega.
    detectados = _nuevos_de(resultado) or {}
    pendientes_antes = estado.get("nuevos_pendientes") or {}
    fusion = _fusionar_nuevos(pendientes_antes, detectados)
    if fusion != pendientes_antes:
        if fusion:
            estado["nuevos_pendientes"] = fusion
        else:
            estado.pop("nuevos_pendientes", None)
        cambio = True
    if fusion:
        if await _enviar_telegram(_mensaje_nuevos(fusion)):
            if estado.pop("nuevos_pendientes", None) is not None:
                cambio = True
        # si falla: se queda fusionado en el estado (ya guardado arriba) --
        # la corrida siguiente lo reintenta, sumándole lo que aparezca nuevo
        # mientras tanto.

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
    """A-2 (auditoría adversarial, 2026-09-27): antes, si `sync_all()`
    reventaba de una manera que su propio try/except interno NO cubre (la
    DB caída al conectar, un import roto, un bug real en
    `_facetas_en_riesgo`), la excepción se propagaba, Python imprimía el
    traceback y el proceso salía en 1 -- en rojo, pero MUDO: nadie se
    entera hasta que alguien mira el journal a mano. Ahora cualquier
    excepción de ese tramo también avisa, con un mensaje que dice
    explícitamente que el propio vigilante falló al correr (distinto de "el
    catálogo tiene problemas": acá ni siquiera se llegó a terminar el
    sync)."""
    try:
        resultado = asyncio.run(_correr())
    except Exception as e:  # fail-soft: un vigilante que revienta sin avisar es peor que uno que sale rojo avisando (A-2)
        motivo = texto_de_error(e)
        logger.exception("catalogo_modelos_ejecutor: sync_all() reventó de forma inesperada")
        print(f"catalogo_modelos ok=False crash={motivo}")
        try:
            asyncio.run(_enviar_telegram(_mensaje_fallo_critico(motivo)))
        except Exception:  # fail-soft: ni el aviso de crash puede impedir salir en rojo
            logger.exception("catalogo_modelos_ejecutor: fallo el aviso de crash")
        return 1

    print(_resumen(resultado))
    try:
        asyncio.run(_avisar(resultado))
    except Exception:  # fail-soft: el código de salida es sobre `ok`, nunca sobre si el aviso salió bien
        logger.exception("catalogo_modelos_ejecutor: _avisar falló, el código de salida no cambia por esto")
    return 0 if resultado["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
