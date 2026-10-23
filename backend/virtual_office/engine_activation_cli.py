"""Explicit, repeatable activation of a project-private Office engine.

The command reads the provider, model and active credential from the durable
catalogue.  It never accepts a provider URL or API key as an argument.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re

import aiomysql

from db_connect_config import db_connect_timeout_seconds
from virtual_office.engine_resolver import engine_binding_fingerprint

_CLASSIFICATION = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_KEY = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_OFFICE_PROVIDER_ID = "zhipu"


def parse_classifications(value: str) -> frozenset[str]:
    values = [part.strip() for part in value.split(",")]
    if not values or any(not _CLASSIFICATION.fullmatch(item) for item in values):
        raise ValueError("classifications must be comma-separated uppercase identifiers")
    result = frozenset(values)
    if len(result) != len(values):
        raise ValueError("classifications must be unique")
    if "INTERNAL_CONFIDENTIAL" not in result:
        raise ValueError("Office engine must allow INTERNAL_CONFIDENTIAL")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="activate-office-engine")
    parser.add_argument("--engine-key", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--classifications", required=True)
    parser.add_argument("--activate", action="store_true", required=True)
    return parser


def _settings() -> dict[str, object]:
    required = ("JAX_DB_HOST", "JAX_DB_PORT", "JAX_DB_USER", "JAX_DB_PASSWORD", "JAX_DB_NAME")
    values = {name: os.environ.get(name, "") for name in required}
    if any(not str(value) for value in values.values()):
        raise ValueError("JAX DB connection settings are required")
    return {"host": values["JAX_DB_HOST"], "port": int(str(values["JAX_DB_PORT"])),
            "user": values["JAX_DB_USER"], "password": values["JAX_DB_PASSWORD"],
            "db": values["JAX_DB_NAME"], "charset": "utf8mb4", "autocommit": False,
            "connect_timeout": db_connect_timeout_seconds()}


async def _catalogue_fingerprints(cur, provider_id: str, model_id: str) -> tuple[int, int, str, str, str]:
    # Lock hierarchy mirrors credential rotation: provider, then the precise
    # credential, then (in activate) the Office engine row.  This avoids an
    # activation fingerprinting a credential while a rotation changes it.
    if provider_id != _OFFICE_PROVIDER_ID:
        raise ValueError("Office v1 requires the zhipu provider")
    await cur.execute("SELECT id,base_url,auth_type,api_key_transport,status,is_local FROM provider WHERE id=%s AND status='active' AND auth_type='api_key' FOR UPDATE", (provider_id,))
    provider = await cur.fetchone()
    if provider is None:
        raise ValueError("provider/model is unavailable")
    if provider[3] != "header_bearer":
        raise ValueError("provider api_key_transport is unavailable")
    await cur.execute("SELECT id,provider_id,model_id,status,is_alias,context_window,supports_tool_use,supports_structured_output,max_tokens_param,max_output_tokens,input_modalities FROM model WHERE provider_id=%s AND model_id=%s AND status='available' FOR UPDATE", (provider_id, model_id))
    model = await cur.fetchone()
    if model is None:
        raise ValueError("provider/model is unavailable")
    await cur.execute(
        "SELECT id,provider_id,state,activated_at,revoked_at,SHA2(encrypted_value,256) FROM credential "
        "WHERE provider_id=%s AND state='active' ORDER BY activated_at DESC,id DESC FOR UPDATE", (provider_id,))
    credentials = await cur.fetchall()
    if not credentials:
        raise ValueError("provider/model needs an active credential")
    # Rotation deliberately leaves the old secret active for a short overlap.
    # The newest row is deterministic and the provider lock above prevents a
    # concurrent rotation from changing this choice before the engine update.
    credential = credentials[0]
    model_ref, credential_id = int(model[0]), int(credential[0])
    await cur.execute("SELECT SHA2(CAST(JSON_ARRAY('office-engine-provider-v1',%s,%s,%s,%s,%s,%s) AS CHAR),256)", tuple(provider))
    provider_fingerprint = (await cur.fetchone())[0]
    await cur.execute("SELECT SHA2(CAST(JSON_ARRAY('office-engine-model-v1',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) AS CHAR),256)", tuple(model))
    model_fingerprint = (await cur.fetchone())[0]
    await cur.execute("SELECT SHA2(CAST(JSON_ARRAY('office-engine-credential-v1',%s,%s,%s,%s,%s,%s) AS CHAR),256)", tuple(credential))
    credential_fingerprint = (await cur.fetchone())[0]
    return model_ref, credential_id, provider_fingerprint, model_fingerprint, credential_fingerprint


async def activate(args: argparse.Namespace) -> dict[str, object]:
    classifications = parse_classifications(args.classifications)
    if not _KEY.fullmatch(args.engine_key) or not args.display_name.strip() or not args.provider_id or not args.model_id:
        raise ValueError("engine key, display name, provider and model are invalid")
    if args.provider_id != _OFFICE_PROVIDER_ID:
        raise ValueError("Office v1 requires the zhipu provider")
    # Keep the timeout visible at the socket-opening call site: the backend
    # tripwire deliberately rejects an opaque ``**_settings()`` expansion.
    settings = _settings()
    connect_timeout = settings.pop("connect_timeout")
    conn = await aiomysql.connect(**settings, connect_timeout=connect_timeout)
    try:
        async with conn.cursor() as cur:
            model_ref, credential_id, provider_fp, model_fp, credential_fp = await _catalogue_fingerprints(cur, args.provider_id, args.model_id)
            await cur.execute("SELECT model_ref,credential_id,transport,allowed_data_classifications,binding_fingerprint,enabled,activation_epoch,version FROM office_engine WHERE engine_key=%s FOR UPDATE", (args.engine_key,))
            prior = await cur.fetchone()
            desired_classes = json.dumps(sorted(classifications), separators=(",", ":"))
            if prior is not None:
                prior_model_ref, prior_credential_id, prior_transport, prior_classes, prior_fp, prior_enabled, prior_epoch, prior_version = prior
                expected = engine_binding_fingerprint(engine_key=args.engine_key, model_ref=int(prior_model_ref), transport=str(prior_transport), allowed_data_classifications=frozenset(json.loads(prior_classes)), provider_fingerprint=provider_fp, model_fingerprint=model_fp, credential_id=credential_id, credential_fingerprint=credential_fp, engine_enabled=int(prior_enabled), activation_epoch=int(prior_epoch), version=int(prior_version))
                unchanged = (int(prior_model_ref) == model_ref and int(prior_credential_id) == credential_id and str(prior_transport) == "http_openai_compat" and prior_classes == desired_classes and int(prior_enabled) == 1 and str(prior_fp) == expected)
                if unchanged:
                    await conn.rollback()
                    return {"engine_key": args.engine_key, "activated": True, "changed": False, "version": int(prior_version)}
                epoch, version = int(prior_epoch) + 1, int(prior_version) + 1
            else:
                epoch, version = 1, 1
            fingerprint = engine_binding_fingerprint(engine_key=args.engine_key, model_ref=model_ref, transport="http_openai_compat", allowed_data_classifications=classifications, provider_fingerprint=provider_fp, model_fingerprint=model_fp, credential_id=credential_id, credential_fingerprint=credential_fp, engine_enabled=1, activation_epoch=epoch, version=version)
            if prior is None:
                await cur.execute("INSERT INTO office_engine (engine_key,display_name_key,model_ref,credential_id,transport,allowed_data_classifications,binding_fingerprint,enabled,activation_epoch,version) VALUES (%s,%s,%s,%s,'http_openai_compat',%s,%s,TRUE,%s,%s)", (args.engine_key, args.display_name.strip(), model_ref, credential_id, desired_classes, fingerprint, epoch, version))
            else:
                await cur.execute("UPDATE office_engine SET display_name_key=%s,model_ref=%s,credential_id=%s,transport='http_openai_compat',allowed_data_classifications=%s,binding_fingerprint=%s,enabled=TRUE,activation_epoch=%s,version=%s WHERE engine_key=%s", (args.display_name.strip(), model_ref, credential_id, desired_classes, fingerprint, epoch, version, args.engine_key))
        await conn.commit()
        return {"engine_key": args.engine_key, "activated": True, "changed": True, "version": version}
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    try:
        result = asyncio.run(activate(_parser().parse_args()))
    except (ValueError, OSError, aiomysql.MySQLError) as error:
        print(str(error), file=os.sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
