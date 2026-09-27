"""
Catalogo de modelos — Bloque D (D1.2/D1.3/D1.4). Ver
jax-platform/docs/fase2-facetas-diseno.md.

REGLA DE ORO (D1.3): este modulo SOLO escribe en `model`. Nunca hace UPDATE
directo a `facet_binding` — un cambio de produccion pasa siempre por una fila
en `model_binding_proposal` + aprobacion explicita desde el admin
(api/admin/models.py). Ningun test de este modulo debe ver facet_binding
mutar fuera de sus columnas resolved_version/resolved_version_checked_at
(D1.2), que son observacion, no produccion.
"""
import json
import logging
import os
import re
import time

from credential_resolver import resolve_credential
from db.connection import get_pool
from http_client import cabeceras_gemini, get_http_client
from redaccion import redactar_secretos, texto_de_error

logger = logging.getLogger("model_catalog")

MODELS_DEV_URL = "https://models.dev/api.json"
DEPRECATION_MISS_THRESHOLD = 3  # D1.4: 3 syncs consecutivos ausente -> deprecated. Nunca 'gone' automatico.

# Proveedores con catalogo real hoy. anthropic (2026-08-10): sync contra
# /v1/models, credencial via CLAUDE_CODE_OAUTH_TOKEN o el OAuth local de
# Claude Code. ollama (2026-08-10): sync local contra /api/tags, sin ninguna
# credencial (provider.auth_type='none') — ver ramas explicitas en
# sync_provider_models. Vive ACA (no en api/admin/models.py, donde nacio) por
# `sync_all()`: la lista de "que se sincroniza" es del dominio del sync, no
# de la capa HTTP, y api/admin/models.py la importa de aca — una sola fuente,
# nunca dos listas que puedan desincronizarse.
SYNCABLE_PROVIDERS = ["openai", "deepseek", "gemini", "moonshot", "zhipu", "anthropic", "ollama"]

# anthropic no tiene fila en `credential` (Hyde no gestiona API key via
# admin/keys.py — ver provider.auth_type='subprocess'). El sync usa en su
# lugar el token OAuth que el propio `claude` CLI ya deja fresco en este
# archivo cada vez que Hyde corre. Decision 2026-08-10 (conversacion con
# Fernando): leer en caliente, sin refresh OAuth propio — un bug ahi
# arriesgaria la sesion en vivo de Hyde por una ganancia menor (el sync
# reintenta solo en la proxima corrida). Ver CONTEXT.md 2026-08-10.
#
# 2026-09-27: desde el 17-sep los servicios corren como `jaxsvc`
# (HOME=/var/lib/jaxsvc), que no tiene `~/.claude/.credentials.json` -- el
# sync de anthropic se saltaba en SILENCIO (devolvia 'skipped' y el
# endpoint seguia contestando ok:true) y Opus 5.5 nunca entro al catalogo
# sin que nadie se enterara. Decision de Fernando: nada de API key de
# Anthropic -- la credencial es SU cuenta Max via `claude setup-token`,
# guardada como CLAUDE_CODE_OAUTH_TOKEN en /etc/jax/.env (mismo NOMBRE que
# ya honra el propio CLI de Claude Code, sin inventar una variable nueva).
# Se prueba PRIMERO -- unica fuente para el nombre de la variable, para que
# no queden dos lugares del codigo que puedan desincronizarse sobre como se
# llama. Nunca se loguea el valor ni un fragmento de ninguno de los dos
# caminos.
ANTHROPIC_OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
_ANTHROPIC_CREDENTIALS_PATH = os.path.expanduser("~/.claude/.credentials.json")
_ANTHROPIC_API_VERSION = "2023-06-01"  # requerido por /v1/models, verificado con curl real


class AnthropicOAuthUnavailableError(Exception):
    """Fail-soft: sin token OAuth local utilizable (archivo ausente, JSON
    invalido, o vencido). El llamador debe saltar el sync de este provider
    con motivo explicito, nunca reintentar con un valor viejo/vacio."""


def _read_anthropic_oauth_token() -> str:
    # Camino de produccion (jaxsvc): la variable de entorno, si trae algo mas
    # que espacios. Vacia ("CLAUDE_CODE_OAUTH_TOKEN=" sin valor) NO cuenta
    # como puesta -- cae al archivo, igual que si la variable no existiera:
    # tratarla como token real seria el mismo defecto (fallo silencioso) que
    # esto viene a arreglar, solo que con un valor vacio en vez de ausente.
    env_token = os.environ.get(ANTHROPIC_OAUTH_TOKEN_ENV, "").strip()
    if env_token:
        return env_token

    # Camino de desarrollo (Hyde, sesion interactiva de Claude Code): el
    # archivo que el propio CLI mantiene fresco.
    try:
        with open(_ANTHROPIC_CREDENTIALS_PATH) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise AnthropicOAuthUnavailableError(f"credentials file unreadable: {type(e).__name__}") from e

    oauth = data.get("claudeAiOauth") or {}
    token = oauth.get("accessToken")
    expires_at = oauth.get("expiresAt")
    if not token or not expires_at:
        raise AnthropicOAuthUnavailableError("credentials file missing accessToken/expiresAt")
    if expires_at / 1000 <= time.time():
        raise AnthropicOAuthUnavailableError("access token expired, esperando que Hyde lo renueve")
    return token

# provider_id (nuestro, en `provider`) -> clave real en models.dev/api.json.
# Verificado contra la API real con curl (2026-08-09), no supuesto: gemini,
# moonshot y zhipu NO coinciden con las claves de models.dev.
_MODELS_DEV_PROVIDER_MAP = {
    "openai": "openai",
    "deepseek": "deepseek",
    "gemini": "google",
    "moonshot": "moonshotai",
    "zhipu": "zai",
}

_VALID_MODALITIES = ("text", "image", "audio", "video")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _valid_date(value):
    """models.dev a veces devuelve 'YYYY-MM' (sin dia) — MySQL DATE bajo
    STRICT_TRANS_TABLES rechaza eso y abortaba todo el loop de enrich
    (bug real 2026-08-10, dejaba sin re-sincronizar el precio de todo lo
    que venia despues en la iteracion). Una fecha mal formada se trata
    como ausente, nunca se fabrica un dia."""
    if value and _DATE_RE.match(value):
        return value
    return None


def _extract_model_ids(provider_id: str, payload: dict) -> list[str]:
    """Gemini responde {'models':[{'name':'models/<id>', ...}]}; los otros 4
    proveedores (OpenAI-compatible) responden {'data':[{'id':<id>}, ...]} —
    misma asimetria real ya vista en api/admin/keys.py:158-166."""
    if provider_id == "gemini":
        return [m["name"].split("/")[-1] for m in payload.get("models", [])]
    return [m["id"] for m in payload.get("data", [])]


# A-3 (auditoría adversarial del commit d549335, 2026-09-27). VERIFICADO EN
# PRODUCCIÓN por la sesión principal el 26-sep: Gemini quedó con
# exactamente 50 modelos 'available' -- el pageSize por defecto de su API --
# y el resto se degradó solo, 3 misses después de 'deprecated', porque el
# sync nunca pedía una segunda página. `limit`/`pageSize` grandes (1000) más
# el cursor de cada API cubren cualquier catálogo real de hoy sin adivinar
# un número "seguro" más chico.
_PAGE_LIMIT_ANTHROPIC = 1000
_PAGE_SIZE_GEMINI = 1000

# MAJOR-1 (segunda auditoría adversarial, 2026-09-27): paginación sin tope.
# 50 páginas x 1000 por página = 50.000 modelos -- ningún catálogo real de
# hoy se acerca a eso; si un proveedor pide más páginas que esto, algo está
# roto (o es hostil) y no una cuenta legítima con muchos modelos.
_TOPE_PAGINAS = 50


class PaginacionSospechosaError(RuntimeError):
    """La paginación de un proveedor no converge: más de `_TOPE_PAGINAS`
    páginas, un cursor (`after_id`/`pageToken`) que se repite sin avanzar, o
    una página vacía con más páginas anunciadas. Se deja propagar sin
    atrapar desde `_fetch_*_paginado`/`sync_provider_models` -- el try/except
    por proveedor de `sync_all()` la cuenta como error de ESE proveedor,
    igual que cualquier otra excepción, nunca como un loop infinito real."""


async def _fetch_anthropic_paginado(client, url: str, headers: dict) -> dict:
    """GET /v1/models de Anthropic pagina con `limit`/`after_id`, y la
    respuesta trae `has_more`/`last_id` (contrato real de su Admin API,
    documentado por el equipo que pidió este fix -- no inventado). Una
    respuesta SIN 'has_more' (los fakes de test_model_catalog_sync.py,
    escritos antes de este cambio) se trata como 'False': una sola página,
    compatibilidad hacia atrás sin tocar esos tests.

    MAJOR-1: corta con `PaginacionSospechosaError` si se superan
    `_TOPE_PAGINAS`, si `last_id` no avanza entre dos páginas con
    `has_more=true`, o si una página llega vacía pero `has_more` sigue
    siendo verdadero -- las tres son señales de una paginación que nunca
    converge, no de un catálogo enorme."""
    datos = []
    after_id = None
    paginas = 0
    while True:
        paginas += 1
        if paginas > _TOPE_PAGINAS:
            raise PaginacionSospechosaError(
                f"anthropic: más de {_TOPE_PAGINAS} páginas sin terminar -- paginación rota o catálogo absurdo")
        params = {"limit": _PAGE_LIMIT_ANTHROPIC}
        if after_id:
            params["after_id"] = after_id
        resp = await client.get(url, headers=headers, params=params, timeout=15.0)
        resp.raise_for_status()
        pagina = resp.json()
        pagina_datos = pagina.get("data", [])
        datos.extend(pagina_datos)
        if not pagina.get("has_more"):
            break
        if not pagina_datos:
            raise PaginacionSospechosaError("anthropic: página vacía con has_more=true")
        nuevo_after_id = pagina.get("last_id")
        if not nuevo_after_id or nuevo_after_id == after_id:
            raise PaginacionSospechosaError(
                f"anthropic: last_id no avanzó ({nuevo_after_id!r}) con has_more=true")
        after_id = nuevo_after_id
    return {"data": datos}


async def _fetch_gemini_paginado(client, url: str, headers: dict) -> dict:
    """models.list de Gemini pagina con `pageSize`/`pageToken`, y la
    respuesta trae `nextPageToken` mientras queden páginas (contrato real de
    la Generative Language API). Sin 'nextPageToken' -- los fakes ya
    existentes -- una sola página, mismo criterio que el de Anthropic.

    MAJOR-1: mismas tres protecciones que la paginación de Anthropic --
    tope de páginas, `pageToken` que no avanza, página vacía con
    `nextPageToken` todavía presente."""
    modelos = []
    page_token = None
    paginas = 0
    while True:
        paginas += 1
        if paginas > _TOPE_PAGINAS:
            raise PaginacionSospechosaError(
                f"gemini: más de {_TOPE_PAGINAS} páginas sin terminar -- paginación rota o catálogo absurdo")
        params = {"pageSize": _PAGE_SIZE_GEMINI}
        if page_token:
            params["pageToken"] = page_token
        resp = await client.get(url, headers=headers, params=params, timeout=15.0)
        resp.raise_for_status()
        pagina = resp.json()
        pagina_modelos = pagina.get("models", [])
        modelos.extend(pagina_modelos)
        nuevo_page_token = pagina.get("nextPageToken")
        if not nuevo_page_token:
            break
        if not pagina_modelos:
            raise PaginacionSospechosaError("gemini: página vacía con nextPageToken presente")
        if nuevo_page_token == page_token:
            raise PaginacionSospechosaError(
                f"gemini: nextPageToken no avanzó ({nuevo_page_token!r})")
        page_token = nuevo_page_token
    return {"models": modelos}


def _motivo_si_respuesta_sospechosa(seen_ids: set) -> str | None:
    """Tercera auditoría adversarial (2026-09-27): SIMPLIFICADO -- se quitó
    por completo el guardián de "menos de la mitad" (denominador frágil,
    guardián que se podía quedar bloqueado, y el mecanismo de `forzar` que
    traía para destrabarlo). Lo único que se conserva: una respuesta 200 con
    la lista VACÍA es sospechosa de un límite/paginación rota (el caso real
    de Gemini que originó todo esto) y se trata como FALLO del proveedor,
    sin sumar un solo miss -- un retiro masivo LEGÍTIMO de modelos (el
    proveedor de verdad se quedó sin ninguno) fluye por los misses normales
    de D1.4 en la próxima corrida, no por acá. Devuelve el motivo (para
    loguear/reportar) o None si la respuesta es de fiar."""
    if not seen_ids:
        return "lista vacía"
    return None


async def sync_provider_models(provider_id: str) -> dict:
    """D1.3-a. Unica verdad de disponibilidad para ESTA cuenta. Upsert en
    `model` para lo visto; lo que no aparecio suma un miss (D1.4)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT api_key_transport, models_list_url FROM provider WHERE id=%s",
                (provider_id,),
            )
            row = await cur.fetchone()

    if not row or not row[1]:
        return {"provider_id": provider_id, "fetched": 0, "skipped": "sin models_list_url"}
    transport, url = row

    if provider_id == "ollama":
        return await _sync_ollama_models(url)

    if provider_id == "anthropic":
        try:
            credential = _read_anthropic_oauth_token()
        except AnthropicOAuthUnavailableError as e:
            logger.warning(f"model_catalog sync provider=anthropic oauth_unavailable reason={e}")
            return {"provider_id": provider_id, "fetched": 0, "skipped": f"oauth local no disponible: {e}"}
    else:
        credential = await resolve_credential(provider_id)

    client = await get_http_client()
    if transport == "header_goog_api_key":
        # T6-2 (2026-09-15): Gemini, key en la cabecera x-goog-api-key.
        # A-3: pagina con pageSize/pageToken (ver _fetch_gemini_paginado).
        payload = await _fetch_gemini_paginado(client, url, cabeceras_gemini(credential))
    elif transport == "query_param":
        # Fail-closed: el valor viejo ponia la key en la URL. La migracion
        # _migrar_gemini_a_cabecera lo reemplaza; una fila que igual lo
        # tenga no vuelve a filtrar el secreto -- falla, y sync_models la
        # reporta como error del provider.
        raise ValueError(
            f"provider={provider_id}: api_key_transport='query_param' ya no se usa "
            "(la key iria en la URL); debe ser 'header_goog_api_key'")
    else:
        headers = {"Authorization": f"Bearer {credential}"}
        if provider_id == "anthropic":
            headers["anthropic-version"] = _ANTHROPIC_API_VERSION
            # A-3: pagina con limit/after_id (ver _fetch_anthropic_paginado).
            payload = await _fetch_anthropic_paginado(client, url, headers)
        else:
            # openai/deepseek/moonshot/zhipu: se autodescriben compatibles
            # con /v1/models de OpenAI (ver _PROVIDER_SYNC_SEED en
            # db/migrations.py), que no pagina -- NO VERIFICADO con curl real
            # contra las 4 APIs desde este entorno (sin credenciales ni red
            # de producción a mano); inferido de la compatibilidad declarada.
            resp = await client.get(url, headers=headers, timeout=15.0)
            resp.raise_for_status()
            payload = resp.json()

    seen_ids = set(_extract_model_ids(provider_id, payload))

    motivo_sospechoso = _motivo_si_respuesta_sospechosa(seen_ids)
    if motivo_sospechoso:
        logger.warning(f"model_catalog sync provider={provider_id} respuesta sospechosa: {motivo_sospechoso}")
        return {"provider_id": provider_id, "error": f"respuesta sospechosa del proveedor ({motivo_sospechoso})"}

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # `nuevos` (2026-09-27): lo que este sync ve por primera vez para
            # ESTE proveedor -- se mide ANTES del upsert de abajo (que ya
            # sembraría estos ids), contra el mismo índice que ya usa el
            # UNIQUE KEY uk_provider_model (provider_id, model_id): sin
            # índice nuevo.
            await cur.execute("SELECT model_id FROM model WHERE provider_id=%s", (provider_id,))
            ya_conocidos = {r[0] for r in await cur.fetchall()}
            nuevos = sorted(seen_ids - ya_conocidos)

            for model_id in seen_ids:
                await cur.execute(
                    "INSERT INTO model (provider_id, model_id, status, source, source_checked_at, consecutive_misses) "
                    "VALUES (%s, %s, 'available', 'provider_api', NOW(), 0) "
                    "ON DUPLICATE KEY UPDATE status='available', source='provider_api', "
                    "source_checked_at=NOW(), consecutive_misses=0",
                    (provider_id, model_id),
                )

            await cur.execute(
                "SELECT id, model_id, consecutive_misses FROM model "
                "WHERE provider_id=%s AND status != 'gone'",
                (provider_id,),
            )
            for row_id, model_id, misses in await cur.fetchall():
                if model_id in seen_ids:
                    continue
                if provider_id == "anthropic" and not model_id.startswith("claude-"):
                    # Alias de tier suelto (ej. 'sonnet') -- GET /v1/models
                    # real de Anthropic jamas lo lista, solo los IDs
                    # fechados/fijados detras del alias (verificado con curl,
                    # 2026-08-10). Ausencia estructural, no una senal real de
                    # que el alias dejo de existir -- no cuenta como miss.
                    continue
                new_misses = misses + 1
                new_status = "deprecated" if new_misses >= DEPRECATION_MISS_THRESHOLD else "degraded"
                await cur.execute(
                    "UPDATE model SET consecutive_misses=%s, status=%s WHERE id=%s",
                    (new_misses, new_status, row_id),
                )
        await conn.commit()

    return {"provider_id": provider_id, "fetched": len(seen_ids), "nuevos": nuevos}


async def _capacidades_ollama(url_show: str, model_id: str) -> list[str] | None:
    """capabilities de POST /api/show (p. ej. ["completion","vision"]).
    None si no se pudo saber: el llamador NO toca input_modalities en ese
    caso (no se degrada un dato bueno por un show caido)."""
    client = await get_http_client()
    try:
        resp = await client.post(url_show, json={"model": model_id}, timeout=15.0)
        resp.raise_for_status()
        caps = resp.json().get("capabilities")
    except Exception as e:  # fail-soft: sin /api/show no se sabe la modalidad; se devuelve None y la fila conserva su valor, logueado con el tipo
        logger.warning(f"model_catalog ollama show fallo model={model_id} reason={type(e).__name__}")
        return None
    return caps if isinstance(caps, list) else None


async def _sync_ollama_models(url: str) -> dict:
    """Ollama es local, sin API key (provider.auth_type='none') — /api/tags
    no lleva ningun header, a diferencia de todos los demas providers.
    Shape real distinto (verificado con curl, 2026-08-10):
    {'models':[{'model':<tag>, 'digest':<sha>, ...}]}, no {'data':[...]}
    ni el {'models':[{'name':'models/<id>'}]} de Gemini.

    Captura ademas `digest`: un tag de Ollama es un puntero LOCAL (no un
    alias del lado del proveedor) — puede re-pullearse con pesos distintos
    sin que el tag cambie, algo que ningun otro transporte puede detectar.
    `digest_changed_at` queda NULL en la primera observacion (no hay 'antes'
    con que comparar) y se pobla solo cuando el digest cambia de verdad
    entre dos syncs — logueado como warning, sin generar
    model_binding_proposal (el tag sigue siendo el mismo, no hay un
    model_ref nuevo al que proponer cambiar)."""
    client = await get_http_client()
    try:
        resp = await client.get(url, timeout=15.0)
        resp.raise_for_status()
    except Exception as e:  # fail-soft: Ollama caído no es un catálogo vacío: se devuelve 'skipped' explícito y no se toca ninguna fila de model (ni misses ni deprecated)
        logger.warning(f"model_catalog sync provider=ollama unreachable reason={type(e).__name__}: {e}")
        return {"provider_id": "ollama", "fetched": 0, "skipped": f"ollama no alcanzable: {type(e).__name__}"}

    entries = resp.json().get("models", [])
    seen = {m["model"]: m.get("digest") for m in entries}
    seen_ids = set(seen.keys())

    # Mismo guardián que sync_provider_models -- un /api/tags que responde
    # 200 con 'models': [] no es lo mismo que 'no alcanzable' (eso ya lo
    # cubre el except de arriba); acá SÍ hay respuesta, pero está vacía. Se
    # corta ANTES de pedir /api/show por cada modelo (nada que consultar).
    motivo_sospechoso = _motivo_si_respuesta_sospechosa(seen_ids)
    if motivo_sospechoso:
        logger.warning(f"model_catalog sync provider=ollama respuesta sospechosa: {motivo_sospechoso}")
        return {"provider_id": "ollama", "error": f"respuesta sospechosa del proveedor ({motivo_sospechoso})"}

    pool = await get_pool()

    # Frente D (2026-09-16): la modalidad de entrada sale de /api/show, ANTES
    # de tomar la conexion (no se retiene una conexion del pool durante HTTP).
    # Misma base que models_list_url (la fila `provider` de ollama), no una
    # URL nueva.
    url_show = url.rsplit("/api/tags", 1)[0] + "/api/show"
    modalidades: dict[str, str] = {}
    for model_id in seen:
        caps = await _capacidades_ollama(url_show, model_id)
        if caps is not None:
            modalidades[model_id] = "text,image" if "vision" in caps else "text"

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # `nuevos` (2026-09-27): mismo contrato que la rama OpenAI-
            # compatible de sync_provider_models -- medido ANTES del upsert.
            await cur.execute("SELECT model_id FROM model WHERE provider_id='ollama'")
            ya_conocidos = {r[0] for r in await cur.fetchall()}
            nuevos = sorted(seen_ids - ya_conocidos)

            for model_id, digest in seen.items():
                await cur.execute(
                    "SELECT digest FROM model WHERE provider_id='ollama' AND model_id=%s",
                    (model_id,),
                )
                prev_row = await cur.fetchone()
                prev_digest = prev_row[0] if prev_row else None
                digest_changed = prev_digest is not None and digest is not None and prev_digest != digest
                if digest_changed:
                    logger.warning(
                        f"model_catalog ollama digest changed model={model_id} "
                        f"from={prev_digest[:12]} to={digest[:12]}"
                    )

                await cur.execute(
                    "INSERT INTO model (provider_id, model_id, status, source, source_checked_at, "
                    "consecutive_misses, digest, digest_changed_at) "
                    "VALUES ('ollama', %s, 'available', 'provider_api', NOW(), 0, %s, NULL) "
                    "ON DUPLICATE KEY UPDATE status='available', source='provider_api', "
                    "source_checked_at=NOW(), consecutive_misses=0, digest=VALUES(digest)"
                    + (", digest_changed_at=NOW()" if digest_changed else ""),
                    (model_id, digest),
                )

                if model_id in modalidades:
                    await cur.execute(
                        "UPDATE model SET input_modalities=%s "
                        "WHERE provider_id='ollama' AND model_id=%s",
                        (modalidades[model_id], model_id),
                    )

            await cur.execute(
                "SELECT id, model_id, consecutive_misses FROM model "
                "WHERE provider_id='ollama' AND status != 'gone'"
            )
            for row_id, model_id, misses in await cur.fetchall():
                if model_id in seen_ids:
                    continue
                new_misses = misses + 1
                new_status = "deprecated" if new_misses >= DEPRECATION_MISS_THRESHOLD else "degraded"
                await cur.execute(
                    "UPDATE model SET consecutive_misses=%s, status=%s WHERE id=%s",
                    (new_misses, new_status, row_id),
                )
        await conn.commit()

    return {"provider_id": "ollama", "fetched": len(seen_ids), "nuevos": nuevos}


async def enrich_from_models_dev() -> dict:
    """D1.3-b. Enriquecimiento: llena metadata (contexto, precio, tool_use,
    modalidades, fechas). JAMAS toca `source`/`status`/existencia de una
    fila — eso es dominio exclusivo de la capa (a); si (a) ya establecio el
    dato con source='provider_api', esta funcion no lo puede degradar
    porque ni siquiera toca esa columna. Formato real verificado contra
    https://models.dev/api.json (curl, 2026-08-09):
    payload[<clave>]['models'][<model_id>] con 'limit.context',
    'cost.input/output/cache_read', 'tool_call', 'release_date',
    'modalities.input'."""
    client = await get_http_client()
    resp = await client.get(MODELS_DEV_URL, timeout=15.0)
    resp.raise_for_status()
    payload = resp.json()

    pool = await get_pool()
    enriched = 0
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT DISTINCT provider_id FROM model")
            our_providers = [r[0] for r in await cur.fetchall()]

            for provider_id in our_providers:
                dev_key = _MODELS_DEV_PROVIDER_MAP.get(provider_id)
                if not dev_key or dev_key not in payload:
                    continue
                dev_models = payload[dev_key].get("models", {})
                if not dev_models:
                    continue

                await cur.execute("SELECT model_id FROM model WHERE provider_id=%s", (provider_id,))
                our_model_ids = [r[0] for r in await cur.fetchall()]

                for model_id in our_model_ids:
                    dev_model = dev_models.get(model_id)
                    if not dev_model:
                        continue
                    limit = dev_model.get("limit") or {}
                    cost = dev_model.get("cost") or {}
                    modalities = (dev_model.get("modalities") or {}).get("input") or []
                    modalities_set = ",".join(m for m in modalities if m in _VALID_MODALITIES) or None

                    await cur.execute(
                        "UPDATE model SET "
                        "context_window=COALESCE(%s, context_window), "
                        "price_input_per_1m_usd=COALESCE(%s, price_input_per_1m_usd), "
                        "price_output_per_1m_usd=COALESCE(%s, price_output_per_1m_usd), "
                        "price_cache_per_1m_usd=COALESCE(%s, price_cache_per_1m_usd), "
                        "release_date=COALESCE(%s, release_date), "
                        "deprecation_date=COALESCE(%s, deprecation_date), "
                        "supports_tool_use=COALESCE(%s, supports_tool_use), "
                        "supports_structured_output=COALESCE(%s, supports_structured_output), "
                        "input_modalities=COALESCE(%s, input_modalities) "
                        "WHERE provider_id=%s AND model_id=%s",
                        (
                            limit.get("context"), cost.get("input"), cost.get("output"),
                            cost.get("cache_read"), _valid_date(dev_model.get("release_date")),
                            _valid_date(dev_model.get("deprecation_date")), dev_model.get("tool_call"),
                            dev_model.get("structured_output"), modalities_set,
                            provider_id, model_id,
                        ),
                    )
                    enriched += 1
        await conn.commit()

    return {"enriched": enriched}


async def record_resolved_version(facet_key: str, resolved_version: str) -> dict:
    """D1.2 — best-effort, fire-and-forget desde el llamador (ver
    api/chat.py _invoke_facet). Compara contra el ultimo valor observado;
    si cambio, crea un model_binding_proposal (la alerta ES la proposal
    pendiente — decision D1.1: sin tabla de log aparte). La primera
    observacion nunca es drift (no hay 'antes' con que comparar)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT provider_id, model_ref, resolved_version FROM facet_binding "
                "WHERE facet_key=%s AND role='primary'",
                (facet_key,),
            )
            row = await cur.fetchone()
            if not row:
                return {"drift": False, "proposal_id": None}
            provider_id, current_model_ref, previous_resolved = row

            await cur.execute(
                "UPDATE facet_binding SET resolved_version=%s, resolved_version_checked_at=NOW() "
                "WHERE facet_key=%s AND role='primary'",
                (resolved_version, facet_key),
            )

            if previous_resolved is None or previous_resolved == resolved_version:
                await conn.commit()
                return {"drift": False, "proposal_id": None}

            await cur.execute(
                "INSERT IGNORE INTO model (provider_id, model_id, is_alias, status, source, source_checked_at) "
                "VALUES (%s, %s, FALSE, 'available', 'observed', NOW())",
                (provider_id, resolved_version),
            )
            await cur.execute(
                "SELECT id FROM model WHERE provider_id=%s AND model_id=%s",
                (provider_id, resolved_version),
            )
            (proposed_model_ref,) = await cur.fetchone()

            await cur.execute(
                "INSERT INTO model_binding_proposal "
                "(facet_key, current_model_ref, proposed_model_ref, reason, detail) "
                "VALUES (%s, %s, %s, 'drift_detected', %s)",
                (
                    facet_key, current_model_ref, proposed_model_ref,
                    f"resolved_version cambio de '{previous_resolved}' a '{resolved_version}'",
                ),
            )
            proposal_id = cur.lastrowid
        await conn.commit()

    logger.warning(
        f"model_catalog drift facet={facet_key} from={previous_resolved} to={resolved_version} proposal_id={proposal_id}"
    )
    return {"drift": True, "proposal_id": proposal_id}


# --------------------------------------------------------------------------
# Facetas en riesgo (2026-09-27)
# --------------------------------------------------------------------------
#
# facet_binding.model_ref es la FK que el DISPATCH REAL usa para resolver el
# modelo -- verificado contra el codigo, no supuesto: facet_resolver.py:298
# (`JOIN model m ON m.id = b.model_ref`), ejecutor/misiones.py:68 y
# adjuntos/politica.py:24 hacen el MISMO join; facet_resolver.py:284-288
# documenta que `b.model_id` (texto) quedo de solo-lectura desde D1.1 paso 4
# y ya no se usa para resolver nada. Por eso esta funcion resuelve el estado
# SIEMPRE por model_ref -- nunca por el texto -- pero informa si el texto
# divergio (facet_binding.provider_id/model_id vs. el provider_id/model_id
# de la fila que model_ref señala hoy): esa divergencia es evidencia de un
# desincronismo que ningun camino de dispatch ve, y silenciarla seria perder
# la unica señal barata de que existe.
#
# Solo role='primary': es el UNICO rol que algun camino de dispatch real lee
# hoy (mismos tres archivos de arriba). 'fallback_1'/'fallback_2' existen en
# el ENUM de facet_binding.role pero ningun resolver los consulta -- filtrar
# por ellos tambien reportaria facetas "en riesgo" que en los hechos no
# despachan nada (ver DEUDA.md / PENDIENTES.md para si algun dia se activan).
#
# Sin indice nuevo: facet_binding es el catalogo de facetas del ecosistema
# (documentado en otros modulos como "hoy 7 filas", ver adjuntos/politica.py),
# acotado por cuantas facetas existen, no por trafico de usuarios -- un
# escaneo completo de esa tabla es instantaneo y agregar un indice sobre una
# columna de 4 valores en una tabla de un digito de filas seria puro ruido.
_SQL_FACETAS_EN_RIESGO = (
    "SELECT b.facet_key, b.provider_id AS provider_binding, b.model_id AS model_id_binding, "
    "b.model_ref, m.provider_id AS provider_resuelto, m.model_id AS model_id_resuelto, m.status "
    "FROM facet_binding b "
    "JOIN facet f ON f.`key` = b.facet_key "
    "LEFT JOIN model m ON m.id = b.model_ref "
    "WHERE b.role = 'primary' AND f.status = 'active' "
    "AND (b.model_ref IS NULL OR m.status != 'available')"
)


async def _facetas_en_riesgo(cur) -> list[dict]:
    """Bindings 'primary' de una faceta ACTIVA (A-9, auditoría adversarial
    2026-09-27: `facet.status != 'active'` ya no despacha nada -- mismo
    filtro que usa el dispatch real en facet_resolver.py:299 y
    adjuntos/politica.py:26 -- así que una faceta 'disabled'/'degraded'
    atada a un modelo roto no es un riesgo, nadie la va a invocar) cuyo
    modelo -- resuelto por `model_ref`, la FK que el dispatch real usa -- no
    esta disponible, o que no tienen `model_ref` en absoluto (dangling: el
    dispatch real, que hace INNER JOIN, no encontraria nada y la faceta
    quedaria FacetUnavailableError). Ver el comentario de modulo de arriba
    para la evidencia de por que model_ref y no el texto.

    FUERA DE ALCANCE a propósito: `motor_resolved` (api/admin/motors.py) es
    OTRO camino de resolución de modelo, vía `motor.model_ref` con fallback
    a `facet_binding.model_ref` -- no lo cubre esta función. Un motor sin
    faceta homónima que apunte a un modelo roto no aparece acá."""
    await cur.execute(_SQL_FACETAS_EN_RIESGO)
    filas = []
    for (facet_key, provider_binding, model_id_binding, model_ref,
         provider_resuelto, model_id_resuelto, status) in await cur.fetchall():
        fila = {
            "facet_key": facet_key,
            "provider_id": provider_resuelto if model_ref is not None else provider_binding,
            "model_id": model_id_resuelto if model_ref is not None else model_id_binding,
            "status": status if model_ref is not None else "sin_model_ref",
        }
        if model_ref is not None and (
            provider_resuelto != provider_binding or model_id_resuelto != model_id_binding
        ):
            fila["model_ref_diverge_de_texto"] = True
        filas.append(fila)
    return filas


# Tercera auditoría adversarial (2026-09-27), punto 5: candado contra syncs
# concurrentes. `GET_LOCK`/`RELEASE_LOCK` de MariaDB son POR CONEXIÓN (no
# por sesión lógica ni por transacción) -- por eso `sync_all()` reserva UNA
# conexión del pool y la mantiene DEDICADA durante todo el sync; soltarla
# antes de tiempo soltaría el candado antes de tiempo, y otro proceso que
# la reusara (el pool las recicla) heredaría un candado que cree que nadie
# más tiene.
_NOMBRE_CANDADO_SYNC = "jax_catalogo_sync"

#: Respuesta cuando el candado ya lo tiene otro proceso -- no se tocó nada
#: (ni una consulta de escritura corrió). `code='sync_en_curso'` es un
#: código nuevo y claro, no una reutilización de 'sync_con_errores': no es
#: un error del catálogo, es "alguien más ya está sincronizando ahora mismo".
def _respuesta_sync_en_curso() -> dict:
    return {
        "ok": False, "code": "sync_en_curso",
        "providers": [], "enrich": {}, "providers_fallidos": [],
        "providers_saltados": [], "enrich_fallido": False,
        "nuevos": {}, "facetas_en_riesgo": [],
    }


async def sync_all() -> dict:
    """Orquesta el sync completo: capa (a) por cada proveedor de
    SYNCABLE_PROVIDERS, capa (b) de enriquecimiento, y el diagnostico de
    saltados/nuevos/facetas en riesgo. Extraida de POST /admin/models/sync
    (2026-09-27) para que el endpoint Y el ejecutor programado
    (catalogo_modelos_ejecutor.py) compartan la MISMA logica de que
    significa que el catalogo este sano -- Regla Absoluta: una sola fuente,
    nunca dos implementaciones que puedan divergir.

    `ok=False` si CUALQUIERA de estos pasa (hallazgo real, 2026-09-27): un
    provider con error, un provider SALTADO (antes esto no bajaba `ok` --
    asi paso desapercibido que anthropic se saltaba en cada corrida desde
    que los servicios corren como jaxsvc), el enriquecimiento fallido, una
    faceta 'primary' cuyo modelo dejo de estar disponible, o que el candado
    (`GET_LOCK`, ver arriba) ya lo tenga otro sync en curso -- en ESE último
    caso `ok=False` no significa "el catálogo está roto", significa "no se
    intentó nada, probá de nuevo en un rato"; el ejecutor programado lo
    distingue explícitamente y no lo trata como problema (ver
    catalogo_modelos_ejecutor.py)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # timeout=0: no espera -- si alguien más lo tiene, se corta al
            # toque en vez de hacer cola (el timer corre cada 6h; una espera
            # larga acá sólo demoraría un click manual sin ganar nada).
            await cur.execute("SELECT GET_LOCK(%s, 0)", (_NOMBRE_CANDADO_SYNC,))
            (obtenido,) = await cur.fetchone()

        if not obtenido:
            logger.warning("sync_all: candado ocupado por otro sync en curso -- no se tocó nada")
            return _respuesta_sync_en_curso()

        try:
            results = []
            for provider_id in SYNCABLE_PROVIDERS:
                try:
                    results.append(await sync_provider_models(provider_id))
                except Exception as e:  # fail-soft: un provider caido no frena a los demas; su error va en el resultado y apaga ok
                    motivo = texto_de_error(e)
                    logger.warning(f"sync_all provider={provider_id} failed reason={motivo}")
                    results.append({"provider_id": provider_id, "error": redactar_secretos(str(e))[:200]})

            try:
                enrich_result = await enrich_from_models_dev()
            except Exception as e:  # fail-soft: el enriquecimiento es capa (b) opcional; su error va en 'enrich' y apaga ok
                logger.warning(f"sync_all enrich failed reason={texto_de_error(e)}")
                enrich_result = {"error": redactar_secretos(str(e))[:200]}

            providers_fallidos = [r["provider_id"] for r in results if "error" in r]
            # Un provider saltado (sin credencial, sin models_list_url, no
            # alcanzable) NO es un sync exitoso -- es exactamente el hallazgo
            # real que origina este cambio: antes 'skipped' no contaba para
            # nada y anthropic desaparecio del catalogo en silencio.
            providers_saltados = [r["provider_id"] for r in results if "skipped" in r]
            enrich_fallido = "error" in enrich_result
            nuevos = {r["provider_id"]: r["nuevos"] for r in results if r.get("nuevos")}

            async with conn.cursor() as cur:
                facetas_en_riesgo = await _facetas_en_riesgo(cur)

            ok = not (providers_fallidos or providers_saltados or enrich_fallido or facetas_en_riesgo)
            respuesta = {
                "ok": ok,
                "providers": results,
                "enrich": enrich_result,
                "providers_fallidos": providers_fallidos,
                "providers_saltados": providers_saltados,
                "enrich_fallido": enrich_fallido,
                "nuevos": nuevos,
                "facetas_en_riesgo": facetas_en_riesgo,
            }
            if not ok:
                # Se conserva el `code` de Task 3 (2026-09-15) a proposito --
                # ya lo leen tests/test_admin_models_endpoints.py, el
                # frontend (AdminModelCatalog.jsx) y su i18n
                # (t.sync_con_errores en es.js/en.js). Ampliar QUE cuenta
                # como "no ok" (saltados, facetas en riesgo) no exige
                # renombrar el codigo que ya identifica "esta respuesta trae
                # algo que mirar".
                respuesta["code"] = "sync_con_errores"
            return respuesta
        finally:
            async with conn.cursor() as cur:
                await cur.execute("SELECT RELEASE_LOCK(%s)", (_NOMBRE_CANDADO_SYNC,))
