"""Unit contract for the inert Office engine catalogue resolver."""
from __future__ import annotations

import pytest


def _row(**overrides):
    from virtual_office.engine_resolver import engine_binding_fingerprint
    row = {"engine_key": "glm", "display_name_key": "office.engine.glm", "model_ref": 42,
           "transport": "http_openai_compat", "allowed_data_classifications": '["SYSTEM_SMOKE"]',
           "version": 3, "engine_enabled": 1, "activation_epoch": 1, "provider_id": "zhipu", "provider_status": "active",
           "provider_auth_type": "api_key", "model_id": "glm-5.3", "model_status": "available",
           "active_credential_count": 1, "provider_fingerprint": "a" * 64,
           "model_fingerprint": "b" * 64, "credential_fingerprint": "c" * 64}
    row["binding_fingerprint"] = engine_binding_fingerprint(
        engine_key=row["engine_key"], model_ref=row["model_ref"], transport=row["transport"],
        allowed_data_classifications=frozenset(("SYSTEM_SMOKE",)),
        provider_fingerprint=row["provider_fingerprint"], model_fingerprint=row["model_fingerprint"],
        credential_fingerprint=row["credential_fingerprint"], engine_enabled=row["engine_enabled"],
        activation_epoch=row["activation_epoch"], version=row["version"])
    row.update(overrides)
    return row


def test_resolved_engine_exposes_identity_without_secret_or_url():
    from virtual_office.engine_resolver import resolve_office_engine_row
    engine = resolve_office_engine_row(_row(), data_classification="SYSTEM_SMOKE")
    assert (engine.engine_key, engine.provider_id, engine.model_id, engine.model_ref, engine.version) == ("glm", "zhipu", "glm-5.3", 42, 3)
    assert not hasattr(engine, "credential")
    assert not hasattr(engine, "base_url")


@pytest.mark.parametrize("field,value", [("engine_enabled", 0), ("provider_status", "deprecated"),
                                            ("model_status", "gone"), ("active_credential_count", 0),
                                            ("active_credential_count", 2)])
def test_unavailable_catalogue_dependencies_fail_closed(field, value):
    from virtual_office.engine_resolver import OfficeEngineUnavailable, resolve_office_engine_row
    with pytest.raises(OfficeEngineUnavailable):
        resolve_office_engine_row(_row(**{field: value}), data_classification="SYSTEM_SMOKE")


@pytest.mark.parametrize("field", ["provider_fingerprint", "model_fingerprint", "credential_fingerprint"])
def test_mutable_binding_drift_fails_closed(field):
    from virtual_office.engine_resolver import OfficeEngineUnavailable, resolve_office_engine_row
    with pytest.raises(OfficeEngineUnavailable):
        resolve_office_engine_row(_row(**{field: "d" * 64}), data_classification="SYSTEM_SMOKE")


def test_expanding_allowed_data_classifications_with_the_old_digest_fails_closed():
    """A catalogue mutator cannot widen the caller's data authority in place."""
    from virtual_office.engine_resolver import OfficeEngineUnavailable, resolve_office_engine_row
    with pytest.raises(OfficeEngineUnavailable):
        resolve_office_engine_row(
            _row(allowed_data_classifications='["SYSTEM_SMOKE","CLIENT_CONFIDENTIAL"]'),
            data_classification="CLIENT_CONFIDENTIAL",
        )


def test_binding_fingerprint_canonicalizes_classification_order_without_weakening_membership():
    from virtual_office.engine_resolver import engine_binding_fingerprint
    fields = dict(engine_key="glm", model_ref=42, transport="http_openai_compat",
                  provider_fingerprint="a" * 64, model_fingerprint="b" * 64,
                  credential_fingerprint="c" * 64, engine_enabled=1, activation_epoch=1, version=3)
    assert engine_binding_fingerprint(
        **fields, allowed_data_classifications=frozenset(("SYSTEM_SMOKE", "CLIENT_CONFIDENTIAL")),
    ) == engine_binding_fingerprint(
        **fields, allowed_data_classifications=frozenset(("CLIENT_CONFIDENTIAL", "SYSTEM_SMOKE")),
    )


def test_fingerprint_binds_enabled_and_activation_epoch():
    from virtual_office.engine_resolver import OfficeEngineUnavailable, resolve_office_engine_row
    with pytest.raises(OfficeEngineUnavailable):
        resolve_office_engine_row(_row(engine_enabled=0), data_classification="SYSTEM_SMOKE")
    with pytest.raises(OfficeEngineUnavailable):
        resolve_office_engine_row(_row(activation_epoch=2), data_classification="SYSTEM_SMOKE")


def test_real_db_mutants_for_route_and_egress_fields_fail_closed(client):
    """Each mutable SQL input, including a control byte, breaks an approved digest."""
    from db.connection import get_pool
    from virtual_office.engine_resolver import (OfficeEngineUnavailable,
                                                engine_binding_fingerprint,
                                                resolve_office_engine_in_cursor)

    async def scenario():
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # Temporary relations make this a real MariaDB query/mutation proof
                # without changing the fixture's durable provider catalogue.
                await cur.execute("CREATE TEMPORARY TABLE provider (id VARCHAR(64),base_url TEXT,auth_type VARCHAR(32),api_key_transport VARCHAR(32),status VARCHAR(32),is_local TINYINT)")
                await cur.execute("CREATE TEMPORARY TABLE model (id INT,provider_id VARCHAR(64),model_id VARCHAR(128),status VARCHAR(32),is_alias TINYINT,context_window INT,supports_tool_use TINYINT,supports_structured_output TINYINT,max_tokens_param VARCHAR(64),max_output_tokens INT,input_modalities VARCHAR(64))")
                await cur.execute("CREATE TEMPORARY TABLE credential (id INT,provider_id VARCHAR(64),state VARCHAR(32),activated_at DATETIME,revoked_at DATETIME,encrypted_value TEXT)")
                await cur.execute("CREATE TEMPORARY TABLE office_engine (engine_key VARCHAR(64),display_name_key VARCHAR(128),model_ref INT,transport VARCHAR(32),allowed_data_classifications TEXT,binding_fingerprint CHAR(64),enabled TINYINT,activation_epoch BIGINT,version BIGINT)")
                await cur.execute("INSERT INTO provider VALUES (%s,%s,%s,%s,%s,%s)", ("p", "https://a\x1f.example", "api_key", "bearer", "active", 0))
                await cur.execute("INSERT INTO model VALUES (1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", ("p", "m\x1f1", "available", 0, 8192, 1, 1, "max_tokens", 2048, "text"))
                await cur.execute("INSERT INTO credential VALUES (1,%s,%s,NOW(),NULL,%s)", ("p", "active", "ciphertext"))
                await cur.execute("SELECT SHA2(CAST(JSON_ARRAY('office-engine-provider-v1',p.id,p.base_url,p.auth_type,p.api_key_transport,p.status,p.is_local) AS CHAR),256),SHA2(CAST(JSON_ARRAY('office-engine-model-v1',m.id,m.provider_id,m.model_id,m.status,m.is_alias,m.context_window,m.supports_tool_use,m.supports_structured_output,m.max_tokens_param,m.max_output_tokens,m.input_modalities) AS CHAR),256),(SELECT SHA2(CAST(JSON_ARRAY('office-engine-credential-v1',c.id,c.provider_id,c.state,c.activated_at,c.revoked_at,SHA2(c.encrypted_value,256)) AS CHAR),256) FROM credential c WHERE c.provider_id=p.id AND c.state='active' ORDER BY c.id LIMIT 1) FROM provider p JOIN model m ON m.provider_id=p.id")
                provider_fp, model_fp, credential_fp = await cur.fetchone()
                digest = engine_binding_fingerprint(engine_key="glm", model_ref=1, transport="http_openai_compat", allowed_data_classifications=frozenset(("SYSTEM_SMOKE",)), provider_fingerprint=provider_fp, model_fingerprint=model_fp, credential_fingerprint=credential_fp, engine_enabled=1, activation_epoch=1, version=1)
                await cur.execute("INSERT INTO office_engine VALUES (%s,%s,1,%s,%s,%s,1,1,1)", ("glm", "office.engine.glm", "http_openai_compat", '["SYSTEM_SMOKE"]', digest))
                assert (await resolve_office_engine_in_cursor(cur, engine_key="glm", data_classification="SYSTEM_SMOKE")).engine_key == "glm"
                # The fixture pool is autocommit, so each UPDATE ends the
                # savepoint transaction. Restore each field explicitly instead
                # of rolling back a savepoint MariaDB has already discarded.
                # The second assertion proves the restore returned to the
                # approved binding before the next mutation.
                mutations = (
                    ("UPDATE provider SET base_url=%s", ("https://changed.example",), "UPDATE provider SET base_url=%s", ("https://a\x1f.example",)),
                    ("UPDATE provider SET auth_type=%s", ("oauth",), "UPDATE provider SET auth_type=%s", ("api_key",)),
                    ("UPDATE provider SET api_key_transport=%s", ("header-x-api-key",), "UPDATE provider SET api_key_transport=%s", ("bearer",)),
                    ("UPDATE provider SET status=%s", ("disabled",), "UPDATE provider SET status=%s", ("active",)),
                    ("UPDATE provider SET is_local=1", (), "UPDATE provider SET is_local=0", ()),
                    ("UPDATE model SET model_id=%s", ("changed",), "UPDATE model SET model_id=%s", ("m\x1f1",)),
                    ("UPDATE model SET status=%s", ("disabled",), "UPDATE model SET status=%s", ("available",)),
                    ("UPDATE model SET is_alias=1", (), "UPDATE model SET is_alias=0", ()),
                    ("UPDATE model SET context_window=1", (), "UPDATE model SET context_window=8192", ()),
                    ("UPDATE model SET supports_tool_use=0", (), "UPDATE model SET supports_tool_use=1", ()),
                    ("UPDATE model SET supports_structured_output=0", (), "UPDATE model SET supports_structured_output=1", ()),
                    ("UPDATE model SET max_tokens_param=%s", ("limit",), "UPDATE model SET max_tokens_param=%s", ("max_tokens",)),
                    ("UPDATE model SET max_output_tokens=1", (), "UPDATE model SET max_output_tokens=2048", ()),
                    ("UPDATE model SET input_modalities=%s", ("image",), "UPDATE model SET input_modalities=%s", ("text",)),
                    ("UPDATE credential SET encrypted_value=%s", ("changed-ciphertext",), "UPDATE credential SET encrypted_value=%s", ("ciphertext",)),
                )
                try:
                    for statement, params, restore, restore_params in mutations:
                        await cur.execute(statement, params)
                        with pytest.raises(OfficeEngineUnavailable):
                            await resolve_office_engine_in_cursor(cur, engine_key="glm", data_classification="SYSTEM_SMOKE")
                        await cur.execute(restore, restore_params)
                        assert (await resolve_office_engine_in_cursor(
                            cur, engine_key="glm", data_classification="SYSTEM_SMOKE"
                        )).engine_key == "glm"
                finally:
                    # A pool retains sessions.  Without this cleanup the next
                    # test can inherit these shadow relations instead of the
                    # fixture's durable catalogue.
                    for relation in ("office_engine", "credential", "model", "provider"):
                        await cur.execute(f"DROP TEMPORARY TABLE IF EXISTS {relation}")

    client.portal.call(scenario)


def test_malformed_or_duplicate_classifications_and_unallowed_data_fail_closed():
    from virtual_office.engine_resolver import OfficeEngineUnavailable, resolve_office_engine_row
    for classifications, requested in (("[{}]", "SYSTEM_SMOKE"), ('["SYSTEM_SMOKE","SYSTEM_SMOKE"]', "SYSTEM_SMOKE"), ('["SYSTEM_SMOKE"]', "CLIENT_CONFIDENTIAL")):
        with pytest.raises(OfficeEngineUnavailable):
            resolve_office_engine_row(_row(allowed_data_classifications=classifications), data_classification=requested)
