"""Ejecutor programado del catálogo de modelos (2026-09-27, revisado tras
tres rondas de auditoría adversarial -- ver abajo la de 2026-09-28).

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
3. Avisa por Telegram con un envío PROPIO y mínimo (ya no se importa
   `jacobs.reaper.send_telegram_alert` del repo `jax` -- ver el docstring de
   `_enviar_telegram` para el motivo). El dedupe de "problemas" SÓLO avanza
   si Telegram confirmó la entrega: un aviso que falla no se marca como
   avisado, la corrida siguiente reintenta.
4. Un aviso roto (Telegram caído, disco lleno) nunca enmascara el código de
   salida del job.

"nuevos": ya no hay un archivo de pendientes que acumula modelos nuevos sin
avisar -- "es nuevo" lo decide la BASE (`model.created_at`), comparado
contra una MARCA (timestamp) guardada en el archivo de estado. Desde la
cuarta auditoría adversarial (2026-09-28, MINOR-2), la consulta que compara
la marca contra `NOW()` corre DENTRO de `model_catalog.sync_all()`, con el
candado de sync TODAVÍA tomado (para que un sync concurrente no se cuele en
la ventana) -- este módulo sólo decide, con el resultado que le llega, si
avisa y si la marca avanza. La marca sólo avanza cuando Telegram confirmó el
envío (o cuando no había nada que avisar): un crash en cualquier punto no
pierde nada, la corrida siguiente recalcula desde la marca que sigue en
disco. `sync_provider_models` sigue devolviendo su propio `nuevos` (lo que
ESTA corrida vio por primera vez) para la respuesta de POST
/admin/models/sync que lee la UI -- el aviso programado de este módulo ya no
lo usa.

Cuarta auditoría adversarial (2026-09-28) -- código de salida:

- `0`: todo sano (`ok=True`), o un candado ocupado por OTRO sync (UNA sola
  vez -- ver `_manejar_sync_en_curso`).
- `3`: hubo problemas (`ok=False`, sin ser `sync_en_curso`) Y el aviso
  quedó resuelto -- Telegram confirmó la entrega recién ahora, o ya estaba
  avisado y deduplicado con razón (misma firma, dentro de la ventana).
- `1`: cualquier otro desenlace -- el aviso de "problemas" NO se pudo
  confirmar, `_correr()`/`_ciclo()` reventaron de una forma que ni su
  propio try/except cubre, el candado lleva DOS O MÁS corridas seguidas
  ocupado, o ni siquiera se pudo importar `http_client` (.venv roto).

MAJOR-1 (cuarta auditoría adversarial, 2026-09-28): `MONITOR_SERVICE_RESULT`
que systemd exporta a `OnFailure=` vale `exit-code` no sólo cuando ESTE
módulo corrió y decidió su propio código -- TAMBIÉN cubre un fallo de
`chdir` (200), un fallo de `exec` (203), un `.venv` roto (un import que
revienta ANTES de que main() pueda hacer nada), o este mismo proceso
saliendo 1 porque SU PROPIO aviso de Telegram falló. Bajo la regla vieja
("callar si exit-code") los cuatro quedaban en silencio. Por eso el código
de salida real (visible en `MONITOR_EXIT_STATUS`) importa: `ops/
avisar-fallo-unidad.py` sólo calla si el par es EXACTAMENTE
`(exit-code, 3)` -- todo lo demás (incluidos esos cuatro casos) avisa. Por
la misma razón, `close_http_client`/`get_http_client`/`redactar_secretos`/
`texto_de_error` (no-stdlib) ya NO se importan al tope del módulo: si
`http_client`/`redaccion` (o algo que ellos importen) está roto, `main()`
lo atrapa y devuelve 1 de forma controlada, en vez de que el intérprete
muera con un traceback sin que nada de este archivo haya podido decidir
nada. `close_http_client` queda como nombre de módulo (arranca en `None`)
para que los tests lo sigan pudiendo parchear -- `main()` sólo lo resuelve
de verdad si SIGUE siendo `None` (si un test ya lo parcheó, no lo pisa).

Uso: `python -m catalogo_modelos_ejecutor` desde `backend/`, con el mismo
entorno que el resto del servicio (`/etc/jax/.env`) más
`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`. No depende de `JAX_REPO_PATH`: el
envío propio a Telegram no cruza a otro repo.
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

logger = logging.getLogger("catalogo_modelos_ejecutor")

# MAJOR-1 (cuarta auditoría adversarial, 2026-09-28): ver el párrafo grande
# del docstring del módulo -- se resuelve de verdad dentro del try de
# main(), nunca al importar este módulo. Arranca en None a propósito: los
# tests siguen pudiendo `monkeypatch.setattr(ejecutor, "close_http_client",
# ...)` ANTES de llamar a `main()` (ver `_sin_cerrar_el_cliente_http_real`
# en los tests) -- si ya no es None, main() no lo pisa.
close_http_client = None

#: Directorio donde se guarda el estado del último aviso (dedupe de
#: "problemas", marca de "nuevos", contador de candado ocupado). Variable de
#: entorno primero; sin ella, bajo el HOME del proceso -- en producción eso
#: es jaxsvc (/var/lib/jaxsvc), el mismo usuario que corre el resto del
#: servicio.
ESTADO_DIR_ENV = "JAX_CATALOGO_ESTADO_DIR"

#: Credenciales del bot -- las MISMAS variables que ya usa `jax/jacobs/
#: reaper.py::send_telegram_alert` (mismo ecosistema, un solo bot), pero acá
#: se leen y se usan directo: sin importar ese módulo.
TELEGRAM_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID_ENV = "TELEGRAM_CHAT_ID"

#: No se reavisa del MISMO conjunto de "problemas" antes de que pase esto --
#: evita el spam de "sigue roto" en cada intento real de sync (el timer de
#: systemd pasa a correr cada HORA desde 2026-09-27 -- ver ops/ -- pero el
#: gate de `catalogo_sync_config` sólo deja pasar un intento real cada
#: `cada_valor`/`cada_unidad`, 6 horas por defecto; los ticks horarios que el
#: gate cierra ni siquiera llegan a este dedupe, ver `_CODIGOS_GATE_CERRADO`);
#: un problema NUEVO, en cambio, avisa de inmediato. "nuevos" NO usa esta
#: ventana -- ver `_avisar`.
VENTANA_REAVISO_SEGUNDOS = 24 * 60 * 60

#: MINOR-5 (cuarta auditoría adversarial, 2026-09-28): Telegram acepta hasta
#: ~4096 caracteres por mensaje; se deja margen y se recorta ANTES de ese
#: límite real, nunca después.
LIMITE_TELEGRAM = 4000

#: MAJOR-2(c) (cuarta auditoría adversarial, 2026-09-28): un candado ocupado
#: UNA vez no es alarmante (alguien más -- un click manual, u otro intento
#: real que el gate de `catalogo_sync_config` dejó pasar -- lo puede estar
#: usando en este instante); a partir de la SEGUNDA corrida CONSECUTIVA
#: probablemente está trabado de verdad. Este contador SÓLO avanza en
#: intentos reales (nunca en un tick horario que el gate cerró sin tocar
#: nada, ver `_limpiar_contador_sync_en_curso` y `_CODIGOS_GATE_CERRADO`).
CONSECUTIVOS_SYNC_EN_CURSO_ANTES_DE_AVISAR = 2


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
    # Un archivo con JSON válido pero que NO es un objeto (una lista, un
    # string, un número -- corrupción parcial, o alguien lo pisó a mano)
    # rompería cada `estado.get(...)` de más abajo con AttributeError. Se
    # trata igual que "no hay estado".
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
    spam que esto evita. Sólo se usa para "problemas": "nuevos" se compara
    contra una marca, no se dedupea por ventana (ver `_avisar`).

    Quinta auditoría adversarial (2026-09-28), punto A: si `notificado_en`
    quedara DESPUÉS de `ahora` (el reloj del sistema saltó hacia atrás,
    mismo tipo de anomalía que MINOR-3 cubre del lado de la marca de
    "nuevos"), `ahora - notificado_en` da NEGATIVO -- siempre menor que
    `VENTANA_REAVISO_SEGUNDOS`, así que sin este chequeo se leería como
    "todavía dentro de la ventana" y se DEDUPEARÍA un problema que en
    realidad nunca se confirmó avisado con el reloj de hoy. No hay nada
    contra qué comparar con confianza -- se avisa, igual que si no hubiera
    entrada previa."""
    if not entrada:
        return True
    if entrada.get("firma") != firma_actual:
        return True
    notificado_en = entrada.get("notificado_en")
    if not isinstance(notificado_en, (int, float)):
        return True
    if notificado_en > ahora:
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
    """MINOR-5 (cuarta auditoría adversarial, 2026-09-28): si la lista de
    nuevos fuera larga, no se manda un mensaje gigante -- se corta por
    proveedor (nunca a mitad de un nombre de modelo) y se dice cuántos
    quedaron afuera."""
    prefijo = "Catálogo de modelos: modelos nuevos detectados -- "
    total_modelos = sum(len(ids) for ids in nuevos.values())
    incluidas: list[str] = []
    incluidos = 0
    for proveedor, ids in sorted(nuevos.items()):
        linea = f"{proveedor}: {', '.join(ids)}"
        candidato = f"{prefijo}{', '.join(incluidas + [linea])}."
        if incluidas and len(candidato) > LIMITE_TELEGRAM:
            break
        incluidas.append(linea)
        incluidos += len(ids)
    restantes = total_modelos - incluidos
    texto = f"{prefijo}{', '.join(incluidas)}."
    if restantes > 0:
        texto = texto.rstrip(".") + f" (y {restantes} más)."
    return texto


def _mensaje_marca_retrocedio(resultado: dict) -> str:
    """MINOR-3 (cuarta auditoría adversarial, 2026-09-28): `model_catalog.
    sync_all()` marcó `marca_retrocedio` -- `NOW()` de la base dio ANTES que
    la marca guardada (reloj o zona horaria movidos hacia atrás). Es una
    anomalía real: comparar contra una marca "del futuro" dejaría la
    ventana de "nuevos" rota (vacía o invertida) para siempre si no se
    re-fija."""
    return (
        "Catálogo de modelos: el reloj de la base de datos parece haber ido "
        "hacia atrás (o cambió de zona horaria) -- la marca de \"modelos "
        f"nuevos\" se re-fijó a {resultado.get('marca_corte')}. Revisar el "
        "reloj del servidor de MariaDB."
    )


def _mensaje_candado_trabado(consecutivos: int) -> str:
    return (
        "Catálogo de modelos: el candado de sincronización lleva "
        f"{consecutivos} corridas seguidas ocupado -- probablemente esté "
        "trabado. Revisar."
    )


def _mensaje_fallo_critico(motivo: str) -> str:
    return f"Catálogo de modelos: el vigilante falló al correr -- {motivo}"


async def _enviar_telegram(mensaje: str) -> bool:
    """Envío propio y mínimo. ANTES importaba
    `jacobs.reaper.send_telegram_alert` (repo `jax`) -- eso mete en
    `sys.path` un checkout entero de otro repo y arrastra
    `jacobs.store`/`jacobs.policy`/`interruptor`: módulos que pueden chocar
    con algo ya presente en `sys.modules` de ESTE proceso (jax-platform
    tiene módulos propios con nombres parecidos) -- el resultado es un
    import híbrido frágil, que depende del ORDEN en que algo se haya
    importado antes en este mismo proceso, no sólo de qué hay en disco.
    Este envío usa el cliente HTTP YA compartido de jax-platform
    (`http_client.get_http_client`) y no toca `jax` para nada.

    `http_client`/`redaccion` se importan ACÁ ADENTRO (MAJOR-1, cuarta
    auditoría adversarial, 2026-09-28) -- ver el docstring del módulo.

    Devuelve True SÓLO si Telegram confirmó la entrega (200 + body['ok']) --
    nunca "se intentó mandar". `_avisar()` depende de este valor real para
    decidir si el dedupe avanza: antes se marcaba "avisado" aunque el envío
    fallara, y un Telegram caído dejaba el catálogo roto en silencio otras
    24h sin que nadie insistiera.

    El token del bot NUNCA se loguea ni se devuelve: viaja en el PATH de la
    URL (`.../bot<token>/sendMessage`), forma que NINGUNA regla de
    `redactar_secretos` reconoce por defecto (no es un query param ni tiene
    la forma AIza...) -- se pasa `secretos=[token]` EXPLÍCITO, que tapa por
    substring exacto antes de cualquier patrón, tanto en el log de una
    excepción de red como en el cuerpo de una respuesta sin confirmar."""
    from http_client import get_http_client
    from redaccion import redactar_secretos, texto_de_error

    if len(mensaje) > LIMITE_TELEGRAM:
        # Quinta auditoría adversarial (2026-09-28), punto C: el corte tiene
        # que dejar lugar para el propio sufijo -- `mensaje[:LIMITE] +
        # sufijo` daba un mensaje de `LIMITE + len(sufijo)` caracteres,
        # MÁS largo que el límite que se quería respetar.
        sufijo = "… (mensaje recortado)"
        mensaje = mensaje[:LIMITE_TELEGRAM - len(sufijo)] + sufijo

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

    # `resp.json()` puede parsear bien y devolver algo que NO es un objeto
    # (una lista, un número) -- `.get("ok")` reventaría con AttributeError,
    # sin marcar, fuera de cualquier try. Se trata como "no confirmado",
    # igual que cualquier otra respuesta rara.
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


async def _avisar(resultado: dict) -> str:
    """Decide si avisar de "problemas" y de "nuevos", y devuelve el
    DESENLACE de la notificación de problemas (MAJOR-1, cuarta auditoría
    adversarial, 2026-09-28) -- "nuevos" es independiente de `ok` y nunca
    cambia el código de salida del proceso:

    - "sin_problemas": `ok=True`, no había nada que avisar.
    - "avisado": había problemas y Telegram confirmó la entrega RECIÉN
      ahora.
    - "dedupeado": había problemas pero YA se había avisado antes (misma
      firma, dentro de la ventana) -- no hacía falta reintentar.
    - "fallo": había problemas y el envío no se pudo confirmar (ni fresco
      ni deduplicado).

    `main()` usa este valor para decidir entre el código de salida 3
    (avisado/dedupeado) y 1 (fallo) -- nunca 0 con problemas de por medio."""
    ruta = _ruta_estado()
    estado = _cargar_estado(ruta)
    ahora = time.time()

    # --- Problemas: dedupe por firma+ventana, pero el estado SÓLO avanza si
    # Telegram confirmó la entrega. Si falla, no se toca nada: la firma
    # actual sigue "sin avisar" y la corrida siguiente reintenta sola.
    problemas = _problemas_de(resultado)
    if problemas is None:
        estado_aviso = "sin_problemas"
        # Catálogo sano: se limpia el "ya avisado" de la vez pasada -- si el
        # MISMO problema reaparece más adelante, tiene que volver a avisar.
        if "problemas" in estado:
            del estado["problemas"]
            _guardar_estado(ruta, estado)
    else:
        firma = _firma(problemas)
        if _debe_avisar(estado.get("problemas"), firma, ahora):
            if await _enviar_telegram(_mensaje_problemas(resultado)):
                estado["problemas"] = {"firma": firma, "notificado_en": ahora}
                _guardar_estado(ruta, estado)
                estado_aviso = "avisado"
            else:
                # si falla: no se escribe nada -- la firma actual sigue sin
                # figurar como avisada, así que la corrida siguiente
                # reintenta.
                estado_aviso = "fallo"
        else:
            estado_aviso = "dedupeado"

    # --- Nuevos: la fuente de verdad es la BASE, calculada DENTRO de
    # model_catalog.sync_all() (MINOR-2, cuarta auditoría adversarial,
    # 2026-09-28) con el candado todavía tomado -- acá sólo se decide si
    # avisar y qué marca queda en el archivo de estado. Regla uniforme (sin
    # casos especiales para la primera corrida, MINOR-4): la marca avanza a
    # `marca_corte` salvo que HAYA nuevos Y el envío falle -- en ese caso se
    # re-persiste `marca_usada` (la marca CON LA QUE arrancó esta corrida),
    # que en una corrida normal es un no-op (ya estaba en disco) y en el
    # arranque (sin marca previa) es lo que evita perder para siempre un
    # modelo que ya quedó insertado en `model`: su `created_at` nunca va a
    # volver a cumplir `> marca` si la próxima corrida capturara "ahora" de
    # nuevo en vez de retomar el mismo punto de partida.
    marca_corte = resultado.get("marca_corte")
    if marca_corte is not None:
        if resultado.get("marca_retrocedio"):
            try:
                await _enviar_telegram(_mensaje_marca_retrocedio(resultado))
            except Exception:  # fail-soft: la marca se re-fija igual aunque este aviso puntual falle
                logger.exception("catalogo_modelos_ejecutor: fallo el aviso de reloj retrocedido")
            estado["nuevos_marca"] = marca_corte
            _guardar_estado(ruta, estado)
        else:
            nuevos = resultado.get("nuevos_desde_marca") or {}
            if nuevos:
                if await _enviar_telegram(_mensaje_nuevos(nuevos)):
                    estado["nuevos_marca"] = marca_corte
                else:
                    marca_usada = resultado.get("marca_usada")
                    if marca_usada is not None:
                        estado["nuevos_marca"] = marca_usada
                _guardar_estado(ruta, estado)
            else:
                # Nada nuevo -- avanzar no arriesga nada.
                estado["nuevos_marca"] = marca_corte
                _guardar_estado(ruta, estado)

    return estado_aviso


def _resumen(resultado: dict) -> str:
    conteo_nuevos = {p: len(ids) for p, ids in (resultado.get("nuevos") or {}).items()}
    facetas = [f["facet_key"] for f in resultado.get("facetas_en_riesgo") or []]
    code = resultado.get("code")
    # 2026-09-27, hallado en producción al desplegar #167: sin esto, una
    # corrida que el gate saltó imprimía lo mismo que un sync real sano. El
    # `logger.info` que lo explica no se ve porque el ejecutor no configura
    # logging a propósito (el INFO de httpx llevaría la URL con el token del
    # bot de Telegram), así que la distinción tiene que ir en esta línea.
    salto = " (sin sincronizar)" if code in _CODIGOS_GATE_CERRADO else ""
    return (
        f"catalogo_modelos ok={resultado['ok']}"
        + (f" code={code}{salto}" if code else "")
        + f" providers_fallidos={resultado.get('providers_fallidos')} "
        f"providers_saltados={resultado.get('providers_saltados')} "
        f"enrich_fallido={resultado.get('enrich_fallido')} "
        f"nuevos={conteo_nuevos} "
        f"facetas_en_riesgo={facetas}"
    )


#: Códigos que significan "el gate de configuración decidió no correr nada
#: -- ni el candado de trabajo, ni una fila de ejecución, ni ningún aviso".
#: `_ciclo()` los trata como un no-op total (2026-09-27, pedido de Fernando:
#: encender/apagar el timer y elegir cada cuánto corre). El timer de systemd
#: pasa a `OnCalendar=hourly`, así que la MAYORÍA de las corridas van a
#: caer acá -- por eso, a diferencia de `sync_en_curso`, este resultado
#: NUNCA toca el estado de dedupe de "problemas" ni el contador de candado
#: ocupado: un ciclo que decide no correr no es evidencia de que el
#: catálogo esté sano NI de que esté roto.
_CODIGO_PROGRAMADO_APAGADO = "programado_apagado"
_CODIGO_PROGRAMADO_NO_TOCA = "programado_no_toca"
_CODIGOS_GATE_CERRADO = frozenset({_CODIGO_PROGRAMADO_APAGADO, _CODIGO_PROGRAMADO_NO_TOCA})


def _resultado_sin_tocar_nada(code: str) -> dict:
    """Misma FORMA que un `model_catalog.sync_all()` sano y vacío -- para
    que `_resumen()` no explote leyendo claves que no están -- pero con un
    `code` propio que `_ciclo()` reconoce para NO tocar ningún estado
    persistido (ver `_CODIGOS_GATE_CERRADO`)."""
    return {
        "ok": True, "code": code, "providers": [], "enrich": {}, "providers_fallidos": [],
        "providers_saltados": [], "enrich_fallido": False, "nuevos": {}, "facetas_en_riesgo": [],
    }


async def _correr() -> dict:
    """`model_catalog`, `catalogo_sync_config`, `catalogo_sync_registro` y
    `close_pool` se importan ACÁ ADENTRO, no al tope del módulo -- ver el
    párrafo grande del docstring del módulo (MAJOR-1). Si alguno revienta al
    cargarse, la excepción sale de ESTA función, que `_ciclo()` corre con un
    try alrededor.

    Gate de configuración (2026-09-27, pedido de Fernando -- el timer de
    systemd pasa a `OnCalendar=hourly` y ESTA función decide si de verdad
    toca): primero lee `catalogo_sync_config` (si la lectura falla, la
    excepción se deja propagar tal cual -- una config ilegible es un fallo
    real, no se puede callar). Si está deshabilitado, o si no pasó el
    intervalo desde la última actualización EXITOSA (contando también las
    manuales -- `catalogo_sync_registro.ultima_actualizacion_exitosa`), no
    se toca nada en absoluto: ni el candado de trabajo, ni una fila de
    ejecución.

    Si toca correr, calcula la marca de "nuevos" a usar (MINOR-4, cuarta
    auditoría adversarial, 2026-09-28): la que esté guardada en el archivo
    de estado, o -- si todavía no hay ninguna -- `NOW()` de la base
    capturado ANTES de correr el sync, para que lo que entre en ESTA misma
    corrida sí cuente como nuevo. La orquestación real (candado de trabajo,
    avance registrado, marca de "nuevos") vive en
    `catalogo_sync_registro.correr_sync_registrado()`, compartida con el
    endpoint manual -- Regla Absoluta, una sola fuente."""
    import catalogo_sync_config
    import catalogo_sync_registro
    from db.connection import get_pool, close_pool
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                config = await catalogo_sync_config.leer_config(cur)
                # UTC_TIMESTAMP(), no NOW() (MINOR-6): `ultima_exitosa` viene
                # de `catalogo_sync_ejecucion.terminado_en`, escrita en UTC
                # -- comparar contra un NOW() en CST desalinearía el gate por
                # las 6 horas de diferencia de la sesión de MariaDB.
                await cur.execute("SELECT UTC_TIMESTAMP()")
                (ahora,) = await cur.fetchone()
                ultima_exitosa = await catalogo_sync_registro.ultima_actualizacion_exitosa(cur)

        if not config["habilitado"]:
            logger.info("catalogo_modelos_ejecutor: sync programado apagado por configuración -- no se tocó nada")
            return _resultado_sin_tocar_nada(_CODIGO_PROGRAMADO_APAGADO)

        if not catalogo_sync_config.toca_correr(ultima_exitosa, ahora, config["cada_valor"], config["cada_unidad"]):
            logger.info(
                f"catalogo_modelos_ejecutor: todavía no toca (última exitosa={ultima_exitosa}, "
                f"cada {config['cada_valor']} {config['cada_unidad']}) -- no se tocó nada"
            )
            return _resultado_sin_tocar_nada(_CODIGO_PROGRAMADO_NO_TOCA)

        estado = _cargar_estado(_ruta_estado())
        marca_guardada = estado.get("nuevos_marca")
        marca_guardada = marca_guardada if isinstance(marca_guardada, str) and marca_guardada else None

        if marca_guardada is not None:
            marca_usada = marca_guardada
        else:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT NOW()")
                    (marca_before,) = await cur.fetchone()
            marca_usada = str(marca_before)

        resultado = await catalogo_sync_registro.correr_sync_registrado(
            origen="programado", marca_nuevos=marca_usada)
        if resultado.get("code") != "sync_en_curso":
            resultado = dict(resultado)
            resultado["marca_usada"] = marca_usada
        return resultado
    finally:
        # Mismo loop que lo creó (ver db/connection.py) -- cerrar acá evita
        # que la interpretación termine con conexiones a medio cerrar.
        await close_pool()


async def _manejar_sync_en_curso() -> int:
    """MAJOR-2(c) (cuarta auditoría adversarial, 2026-09-28): cuenta
    corridas CONSECUTIVAS con `code == "sync_en_curso"`. La primera no es
    alarmante (sale 0, sin aviso, comportamiento de antes); a partir de la
    `CONSECUTIVOS_SYNC_EN_CURSO_ANTES_DE_AVISAR`-ésima, avisa y devuelve 1
    -- que la unidad quede failed y visible en vez de silenciosamente verde
    corrida tras corrida mientras el candado sigue trabado."""
    ruta = _ruta_estado()
    estado = _cargar_estado(ruta)
    consecutivos = estado.get("sync_en_curso_consecutivos", 0)
    if not isinstance(consecutivos, int) or consecutivos < 0:
        consecutivos = 0
    consecutivos += 1
    estado["sync_en_curso_consecutivos"] = consecutivos
    _guardar_estado(ruta, estado)

    if consecutivos < CONSECUTIVOS_SYNC_EN_CURSO_ANTES_DE_AVISAR:
        return 0

    try:
        await _enviar_telegram(_mensaje_candado_trabado(consecutivos))
    except Exception:  # fail-soft: ni este aviso puede impedir salir en rojo
        logger.exception("catalogo_modelos_ejecutor: fallo el aviso de candado trabado")
    return 1


def _limpiar_contador_sync_en_curso() -> None:
    """Se llama cada vez que el sync SÍ corrió (no `sync_en_curso`) -- corta
    la racha para que el conteo de `_manejar_sync_en_curso` no arrastre
    corridas viejas de antes de que el candado se liberara."""
    ruta = _ruta_estado()
    estado = _cargar_estado(ruta)
    if estado.pop("sync_en_curso_consecutivos", None) is not None:
        _guardar_estado(ruta, estado)


async def _ciclo() -> dict | None:
    """El sync, el aviso (de problemas, de nuevos, o de crash) y el cierre
    del cliente HTTP corren en el MISMO event loop, con un único
    `asyncio.run()` en `main()`: `http_client._client` es un
    `httpx.AsyncClient` GLOBAL atado al loop que lo crea la primera vez, así
    que un segundo `asyncio.run()` reusando ese cliente contra un loop
    NUEVO (con el anterior ya cerrado) es el mismo bug de "Event loop is
    closed" que `db/connection.py` ya resolvió para el pool de aiomysql, con
    el mismo remedio: un solo loop para todo el ciclo de vida del proceso.

    Devuelve el `resultado` de `sync_all()` (con dos claves internas que
    `main()` usa para el código de salida -- `_codigo_salida_forzado` y
    `_estado_aviso_problemas`, ver sus docstrings), o `None` si reventó de
    una manera que ni su propio try/except interno cubre -- en ese caso ya
    mandó su propio aviso de "el vigilante falló" acá adentro, y ya imprimió
    el resumen de una línea."""
    resultado = None
    try:
        resultado = await _correr()
    except Exception as e:  # fail-soft: un vigilante que revienta sin avisar es peor que uno que sale rojo avisando
        from redaccion import texto_de_error
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
            resultado["_codigo_salida_forzado"] = await _manejar_sync_en_curso()
        elif resultado.get("code") in _CODIGOS_GATE_CERRADO:
            # Gate de configuración (2026-09-27): "apagado" o "todavía no
            # toca" -- no se tocó nada, así que tampoco se toca ningún
            # estado persistido: ni el dedupe de "problemas" (`_avisar()`
            # lo BORRARÍA si se llamara con `ok=True` acá, perdiendo el
            # aviso pendiente de la última corrida real que sí encontró
            # algo), ni el contador de candado ocupado (no tiene nada que
            # ver con esto). Exit 0 (ver `main()`: `ok=True` sin
            # `_estado_aviso_problemas` alcanza).
            pass
        else:
            _limpiar_contador_sync_en_curso()
            try:
                resultado["_estado_aviso_problemas"] = await _avisar(resultado)
            except Exception:  # fail-soft: no relanza -- pero SÍ deja marca de que _avisar no terminó bien (punto B, quinta auditoría adversarial, 2026-09-28)
                logger.exception("catalogo_modelos_ejecutor: _avisar falló")
                resultado["_estado_aviso_problemas"] = "fallo"

    # Mismo loop, al final de todo: `close_http_client()` está del lado de
    # http_client.py y no toca el pool (ya cerrado dentro de `_correr()`).
    await close_http_client()
    return resultado


def main() -> int:
    global close_http_client
    try:
        if close_http_client is None:
            from http_client import close_http_client as _close_http_client_real
            close_http_client = _close_http_client_real
    except Exception:  # fail-soft: .venv roto -- se devuelve 1 de forma controlada (MAJOR-1)
        logger.exception("catalogo_modelos_ejecutor: no se pudo importar http_client -- .venv roto")
        return 1

    try:
        resultado = asyncio.run(_ciclo())
    except Exception:  # fail-soft: última red de contención antes de salir del proceso
        logger.exception("catalogo_modelos_ejecutor: _ciclo() reventó de una forma no capturada")
        return 1

    if resultado is None:
        return 1

    forzado = resultado.get("_codigo_salida_forzado")
    if forzado is not None:
        return forzado

    if resultado.get("ok"):
        # Punto B (quinta auditoría adversarial, 2026-09-28): `ok=True` no
        # alcanza si `_avisar()` reventó (p. ej. `_guardar_estado` sin
        # permisos o disco lleno) -- eso significa que ni siquiera se pudo
        # confiar en dejar el estado de dedupe/marca al día, y el propio
        # `_ciclo()` ya marcó `_estado_aviso_problemas="fallo"` en ese caso.
        # Un catálogo sano con su mecanismo de aviso roto no es un job
        # exitoso.
        if resultado.get("_estado_aviso_problemas") == "fallo":
            return 1
        return 0

    # Hubo problemas reales (no `sync_en_curso`, ya manejado arriba): 3 si
    # el aviso quedó resuelto (avisado o deduplicado con razón), 1 en
    # cualquier otro caso -- incluido que `_avisar()` haya reventado, en
    # cuyo caso esta clave vale "fallo" (punto B) o no existe si el propio
    # `_ciclo()` reventó antes de escribirla.
    if resultado.get("_estado_aviso_problemas") in ("avisado", "dedupeado"):
        return 3
    return 1


if __name__ == "__main__":
    sys.exit(main())
