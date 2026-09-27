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
   `_enviar_telegram` para el motivo). El dedupe de "problemas" SÓLO avanza
   si Telegram confirmó la entrega (A-1): un aviso que falla no se marca
   como avisado, la corrida siguiente reintenta.
5. Un aviso roto (Telegram caído, disco lleno) nunca enmascara el código de
   salida del job.

Tercera auditoría adversarial (2026-09-27), SIMPLIFICACIÓN de "nuevos": ya
no hay un archivo de pendientes que acumula modelos nuevos sin avisar --
"es nuevo" lo decide la BASE (`model.created_at`), comparado contra una
MARCA (timestamp) guardada en el archivo de estado, que sólo avanza cuando
Telegram confirmó el envío. Un crash en cualquier punto (antes o después de
mandar el aviso) no pierde nada: la marca en disco sigue siendo la última
confirmada, y la corrida siguiente vuelve a calcular desde ahí -- ver
`_nuevos_desde_marca`/`_correr`/`_avisar`. `sync_provider_models` sigue
devolviendo su propio `nuevos` (lo que ESTA corrida vio por primera vez)
para la respuesta de POST /admin/models/sync que lee la UI -- el aviso
programado de este módulo ya no lo usa.

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

# MAJOR-2 (segunda auditoría adversarial, 2026-09-27): `model_catalog` y
# `db.connection` NO se importan acá arriba -- si el .venv de producción
# quedara roto (una dependencia faltante, un bug de import-time en
# model_catalog.py o algo que él mismo importa), un `import` a nivel de
# módulo reventaría ANTES de que `main()` llegue a correr una sola línea, y
# el `try/except` de `main()` nunca lo vería: el proceso moriría con un
# traceback, sin avisar a nadie. Se importan DENTRO de `_correr()` (que
# `main()` sí llama con un try alrededor) para que ESE camino de fallo
# también dispare el aviso de "el vigilante falló". `http_client` y
# `redaccion` sí quedan acá arriba: son módulos más simples y estables, y el
# propio aviso de fallo los necesita para poder mandar algo -- si esos dos
# estuvieran rotos, no habría nada en este proceso capaz de avisar de todos
# modos (para ESE caso está la unidad `OnFailure=`, que ni siquiera usa el
# .venv, ver ops/avisar-fallo-unidad.py).
from http_client import close_http_client, get_http_client
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
        estado = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    # MINOR-7 (segunda auditoría adversarial, 2026-09-27): un archivo con
    # JSON válido pero que NO es un objeto (una lista, un string, un número
    # -- corrupción parcial, o alguien lo pisó a mano) rompería cada
    # `estado.get(...)` de más abajo con AttributeError. Se trata igual que
    # "no hay estado".
    return estado if isinstance(estado, dict) else {}


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


async def _nuevos_desde_marca(cur, marca: str | None, corte: str) -> dict:
    """Modelos cuyo `model.created_at` cae en (marca, corte] -- la fuente de
    verdad de "es nuevo" para el AVISO programado es la BASE, no lo que
    `sync_provider_models` vio en esta corrida puntual (eso sigue
    disponible en `resultado["nuevos"]`, para el endpoint/UI). Si `marca`
    es None (primera corrida, todavía sin marca guardada) no hay nada
    contra qué comparar -- devuelve vacío; `_avisar` fija la marca en
    `corte` sin avisar retroactivamente de todo lo que ya estaba en la
    base antes de que este mecanismo existiera."""
    if marca is None:
        return {}
    await cur.execute(
        "SELECT provider_id, model_id FROM model WHERE created_at > %s AND created_at <= %s "
        "ORDER BY provider_id, model_id",
        (marca, corte),
    )
    nuevos: dict[str, list[str]] = {}
    for provider_id, model_id in await cur.fetchall():
        nuevos.setdefault(provider_id, []).append(model_id)
    return nuevos


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

    # MAJOR-4 (segunda auditoría adversarial, 2026-09-27): `resp.json()`
    # puede parsear bien y devolver algo que NO es un objeto (una lista, un
    # número) -- `.get("ok")` reventaría con AttributeError, sin marcar,
    # fuera de cualquier try. Se trata como "no confirmado", igual que
    # cualquier otra respuesta rara.
    if not isinstance(cuerpo, dict):
        logger.warning(
            f"catalogo_modelos_ejecutor: Telegram respondió un JSON que no es un objeto (status={resp.status_code})"
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

    if cambio:
        _guardar_estado(ruta, estado)
        cambio = False

    # --- Nuevos: la fuente de verdad es la BASE (tercera auditoría
    # adversarial, 2026-09-27; ver `_nuevos_desde_marca`/`_correr`), no un
    # archivo de pendientes. `_correr()` ya calculó, con el mismo pool antes
    # de cerrarlo, qué hay en (marca_previa, marca_corte] -- acá sólo se
    # decide si avisar y si la marca avanza. La marca SOLO avanza cuando
    # Telegram confirma la entrega (o en la primera corrida, sin marca
    # previa, que se fija sin avisar): un crash en cualquier punto -- antes
    # o después del envío -- no pierde ni repite nada, porque en disco sigue
    # la última marca confirmada y la corrida siguiente recalcula desde ahí.
    marca_corte = resultado.get("marca_corte")
    if marca_corte:
        if resultado.get("marca_previa_era_none"):
            # Primera corrida: sin marca previa no hay nada contra qué
            # comparar -- se fija la marca en "ahora" (=`marca_corte`) SIN
            # avisar retroactivamente de todo lo que ya estaba en la base
            # antes de que este mecanismo existiera.
            if estado.get("nuevos_marca") != marca_corte:
                estado["nuevos_marca"] = marca_corte
                _guardar_estado(ruta, estado)
        else:
            nuevos = resultado.get("nuevos_desde_marca") or {}
            if nuevos and await _enviar_telegram(_mensaje_nuevos(nuevos)):
                estado["nuevos_marca"] = marca_corte
                _guardar_estado(ruta, estado)
            # si no hay nada nuevo, o si el envío falla: la marca NO avanza
            # -- la corrida siguiente vuelve a mirar desde la MISMA marca de
            # siempre (más lo que haya aparecido mientras tanto) y no pierde
            # nada.


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
    """MAJOR-2 (segunda auditoría adversarial, 2026-09-27): `model_catalog`
    y `close_pool` se importan ACÁ ADENTRO, no al tope del módulo -- ver el
    comentario grande junto a los imports. Si `model_catalog` (o algo que él
    importa) revienta al cargarse, la excepción sale de ESTA función, que
    `_ciclo()` corre con un try alrededor.

    Tercera auditoría adversarial (2026-09-27): si `sync_all()` devuelve
    `code == "sync_en_curso"` (candado ocupado por otro sync, ver
    `model_catalog.sync_all`) no se tocó NADA -- se devuelve tal cual, sin
    la consulta de "nuevos desde la marca" de abajo (no hay nada nuevo que
    calcular: esta corrida no sincronizó). `_ciclo()`/`main()` lo tratan
    como no-problema.

    Si sí corrió, se calcula acá -- con el MISMO pool, ANTES de cerrarlo --
    qué modelos son nuevos desde la MARCA guardada en el archivo de estado
    (ver `_nuevos_desde_marca`); `_avisar()` decide con eso si avisa y si la
    marca avanza, sin volver a tocar la base."""
    import model_catalog
    from db.connection import get_pool, close_pool
    try:
        resultado = await model_catalog.sync_all()
        if resultado.get("code") == "sync_en_curso":
            return resultado

        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT NOW(6)")
                (corte,) = await cur.fetchone()
                estado = _cargar_estado(_ruta_estado())
                marca = estado.get("nuevos_marca")
                marca = marca if isinstance(marca, str) and marca else None
                nuevos_desde_marca = await _nuevos_desde_marca(cur, marca, corte)

        resultado = dict(resultado)
        resultado["marca_corte"] = str(corte)
        resultado["marca_previa_era_none"] = marca is None
        resultado["nuevos_desde_marca"] = nuevos_desde_marca
        return resultado
    finally:
        # Mismo loop que lo creó (ver db/connection.py) -- cerrar acá evita
        # que la interpretación termine con conexiones a medio cerrar.
        await close_pool()


async def _ciclo() -> dict | None:
    """MINOR-8 (segunda auditoría adversarial, 2026-09-27): TODO -- el sync,
    el aviso (de problemas o de crash) y el cierre del cliente HTTP -- corre
    en el MISMO event loop, con un único `asyncio.run()` en `main()`. Antes
    cada pieza tenía su propio `asyncio.run()`: `http_client._client` es un
    `httpx.AsyncClient` GLOBAL atado al loop que lo crea la primera vez, así
    que un segundo `asyncio.run()` reusando ese cliente contra un loop
    NUEVO (con el anterior ya cerrado) es el mismo bug de "Event loop is
    closed" que `db/connection.py` ya resolvió para el pool de aiomysql, con
    el mismo remedio: un solo loop para todo el ciclo de vida del proceso.

    Devuelve el `resultado` de `sync_all()`, o `None` si reventó de una
    manera que ni su propio try/except interno cubre (A-2: DB caída al
    conectar, un import roto) -- en ese caso ya mandó su propio aviso de
    "el vigilante falló" acá adentro, y ya imprimió el resumen de una
    línea.

    Tercera auditoría adversarial (2026-09-27): `code == "sync_en_curso"`
    (candado ocupado por otro sync, ver `model_catalog.sync_all`) no es un
    problema -- no se tocó nada, así que no hay nada de qué avisar. Se
    imprime igual el resumen (queda en el journal) pero se salta `_avisar`
    por completo."""
    resultado = None
    try:
        resultado = await _correr()
    except Exception as e:  # fail-soft: un vigilante que revienta sin avisar es peor que uno que sale rojo avisando (A-2)
        motivo = texto_de_error(e)
        logger.exception("catalogo_modelos_ejecutor: sync_all() reventó de forma inesperada")
        print(f"catalogo_modelos ok=False crash={motivo}")
        try:
            await _enviar_telegram(_mensaje_fallo_critico(motivo))
        except Exception:  # fail-soft: ni el aviso de crash puede impedir salir en rojo
            logger.exception("catalogo_modelos_ejecutor: fallo el aviso de crash")

    if resultado is not None:
        print(_resumen(resultado))
        if resultado.get("code") == "sync_en_curso":
            logger.info("catalogo_modelos_ejecutor: candado ocupado por otro sync -- no se tocó nada, no se avisa")
        else:
            try:
                await _avisar(resultado)
            except Exception:  # fail-soft: el código de salida es sobre `ok`, nunca sobre si el aviso salió bien
                logger.exception("catalogo_modelos_ejecutor: _avisar falló, el código de salida no cambia por esto")

    # Mismo loop, al final de todo: `close_http_client()` está del lado de
    # http_client.py y no toca el pool (ya cerrado dentro de `_correr()`).
    await close_http_client()
    return resultado


def main() -> int:
    resultado = asyncio.run(_ciclo())
    if resultado is None:
        return 1
    # Tercera auditoría adversarial (2026-09-27): un sync que no corrió
    # porque otro lo tenía tomado no es un fallo del ejecutor -- sale 0.
    if resultado.get("code") == "sync_en_curso":
        return 0
    return 0 if resultado["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
