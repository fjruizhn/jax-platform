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
import logging

from credential_resolver import resolve_credential_instrumented
from db.connection import get_pool
from http_client import get_http_client

logger = logging.getLogger("model_catalog")

MODELS_DEV_URL = "https://models.dev/api.json"
DEPRECATION_MISS_THRESHOLD = 3  # D1.4: 3 syncs consecutivos ausente -> deprecated. Nunca 'gone' automatico.

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


def _extract_model_ids(provider_id: str, payload: dict) -> list[str]:
    """Gemini responde {'models':[{'name':'models/<id>', ...}]}; los otros 4
    proveedores (OpenAI-compatible) responden {'data':[{'id':<id>}, ...]} —
    misma asimetria real ya vista en api/admin/keys.py:158-166."""
    if provider_id == "gemini":
        return [m["name"].split("/")[-1] for m in payload.get("models", [])]
    return [m["id"] for m in payload.get("data", [])]


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

    credential = await resolve_credential_instrumented(provider_id)
    client = await get_http_client()
    if transport == "query_param":
        resp = await client.get(f"{url}?key={credential}", timeout=15.0)
    else:
        resp = await client.get(url, headers={"Authorization": f"Bearer {credential}"}, timeout=15.0)
    resp.raise_for_status()
    seen_ids = set(_extract_model_ids(provider_id, resp.json()))

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
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
                new_misses = misses + 1
                new_status = "deprecated" if new_misses >= DEPRECATION_MISS_THRESHOLD else "degraded"
                await cur.execute(
                    "UPDATE model SET consecutive_misses=%s, status=%s WHERE id=%s",
                    (new_misses, new_status, row_id),
                )
        await conn.commit()

    return {"provider_id": provider_id, "fetched": len(seen_ids)}


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
                            cost.get("cache_read"), dev_model.get("release_date"),
                            dev_model.get("deprecation_date"), dev_model.get("tool_call"),
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
