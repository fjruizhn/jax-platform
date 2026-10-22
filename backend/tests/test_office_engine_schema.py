"""Physical contract for the migrator-only Office engine catalogue."""
from __future__ import annotations

import pytest

from db.connection import get_pool
from virtual_office.migrations import _upgrade_exact_empty_office_engine_v16, apply_office_migrations


_V16_ENGINE_DDL = (
    "CREATE TABLE office_engine ("
    "engine_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,"
    "display_name_key VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,"
    "model_ref INT NOT NULL,transport ENUM('http_openai_compat') NOT NULL,"
    "allowed_data_classifications JSON NOT NULL,binding_fingerprint CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
    "enabled BOOLEAN NOT NULL DEFAULT FALSE,version BIGINT NOT NULL DEFAULT 1,"
    "created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),"
    "CONSTRAINT fk_office_engine_model FOREIGN KEY (model_ref) REFERENCES model(id),"
    "CONSTRAINT chk_office_engine_key CHECK (engine_key<>''),"
    "CONSTRAINT chk_office_engine_display_name_key CHECK (display_name_key<>''),"
    "CONSTRAINT chk_office_engine_classifications CHECK (JSON_TYPE(allowed_data_classifications)='ARRAY' AND JSON_LENGTH(allowed_data_classifications)>0),"
    "CONSTRAINT chk_office_engine_binding_fingerprint CHECK (binding_fingerprint REGEXP '^[0-9a-f]{64}$'),"
    "CONSTRAINT chk_office_engine_version CHECK (version>=1)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
)


def test_engine_catalogue_is_empty_and_refuses_weakened_homonym(client):
    async def scenario():
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute("SELECT COUNT(*) FROM office_engine")
                assert await cur.fetchone() == (0,)
                await cur.execute("DROP TABLE office_engine")
                await cur.execute("CREATE TABLE office_engine (engine_key VARCHAR(64) NOT NULL PRIMARY KEY) ENGINE=InnoDB")
                with pytest.raises(RuntimeError, match="Office engine catalogue v16 predecessor is not exact and empty"):
                    await apply_office_migrations(cur)
                await cur.execute("DROP TABLE office_engine")
                await apply_office_migrations(cur)
    client.portal.call(scenario)


def test_engine_activation_epoch_prevents_enabled_binding_replay(client):
    async def scenario():
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                await cur.execute("SELECT id FROM model ORDER BY id LIMIT 1")
                model = await cur.fetchone()
                assert model is not None
                await cur.execute("SAVEPOINT engine_epoch")
                await cur.execute(
                    "INSERT INTO office_engine (engine_key,display_name_key,model_ref,transport,allowed_data_classifications,binding_fingerprint) "
                    "VALUES ('epoch-test','office.engine.epoch-test',%s,'http_openai_compat',%s,REPEAT('a',64))",
                    (*model, '["SYSTEM_SMOKE"]'),
                )
                with pytest.raises(Exception) as rejected_without_revision:
                    await cur.execute("UPDATE office_engine SET enabled=1 WHERE engine_key='epoch-test'")
                assert getattr(rejected_without_revision.value, "args", (None,))[0] == 1644
                await cur.execute("UPDATE office_engine SET enabled=1,activation_epoch=1,version=2 WHERE engine_key='epoch-test'")
                await cur.execute("UPDATE office_engine SET enabled=0,version=3 WHERE engine_key='epoch-test'")
                with pytest.raises(Exception) as rename:
                    await cur.execute("UPDATE office_engine SET engine_key='epoch-renamed',version=4 WHERE engine_key='epoch-test'")
                assert getattr(rename.value, "args", (None,))[0] == 1644
                with pytest.raises(Exception) as replay:
                    await cur.execute("UPDATE office_engine SET enabled=1,version=4 WHERE engine_key='epoch-test'")
                assert getattr(replay.value, "args", (None,))[0] == 1644
                with pytest.raises(Exception) as deletion:
                    await cur.execute("DELETE FROM office_engine WHERE engine_key='epoch-test'")
                assert getattr(deletion.value, "args", (None,))[0] == 1644
                await cur.execute("ROLLBACK TO SAVEPOINT engine_epoch")
                await cur.execute("RELEASE SAVEPOINT engine_epoch")
    client.portal.call(scenario)


def test_exact_empty_v16_engine_upgrades_but_rows_or_drift_fail_closed(client):
    async def scenario():
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                for trigger in ("guard_office_engine_insert", "guard_office_engine_update", "guard_office_engine_delete"):
                    await cur.execute(f"DROP TRIGGER {trigger}")
                await cur.execute("DROP TABLE office_engine")
                await cur.execute(_V16_ENGINE_DDL)
                await _upgrade_exact_empty_office_engine_v16(cur)
                await apply_office_migrations(cur)
                await cur.execute("SELECT COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='office_engine' AND COLUMN_NAME='activation_epoch'")
                assert await cur.fetchone() == ("bigint(20) unsigned",)
                for trigger in ("guard_office_engine_insert", "guard_office_engine_update", "guard_office_engine_delete"):
                    await cur.execute("SELECT 1 FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME=%s", (trigger,))
                    assert await cur.fetchone() == (1,)
                # A crash after trigger 2 resumes, then a rerun is idempotent.
                await cur.execute("DROP TRIGGER guard_office_engine_delete")
                await apply_office_migrations(cur)
                await apply_office_migrations(cur)
                # A crash after trigger 1 also resumes.
                await cur.execute("DROP TRIGGER guard_office_engine_update")
                await cur.execute("DROP TRIGGER guard_office_engine_delete")
                await apply_office_migrations(cur)
                # A non-prefix set is never repaired opportunistically.
                await cur.execute("DROP TRIGGER guard_office_engine_insert")
                with pytest.raises(RuntimeError, match="v17 cutpoint is not exact and empty"):
                    await apply_office_migrations(cur)
                await cur.execute("DROP TRIGGER guard_office_engine_update")
                await cur.execute("DROP TRIGGER guard_office_engine_delete")
                await apply_office_migrations(cur)
                for trigger in ("guard_office_engine_insert", "guard_office_engine_update", "guard_office_engine_delete"):
                    await cur.execute(f"DROP TRIGGER {trigger}")
                await cur.execute("DROP TABLE office_engine")
                await cur.execute(_V16_ENGINE_DDL)
                await cur.execute("SELECT id FROM model ORDER BY id LIMIT 1")
                model = await cur.fetchone()
                assert model is not None
                await cur.execute("INSERT INTO office_engine (engine_key,display_name_key,model_ref,transport,allowed_data_classifications,binding_fingerprint) VALUES ('legacy-row','legacy-row',%s,'http_openai_compat',%s,REPEAT('a',64))", (*model, '["SYSTEM_SMOKE"]'))
                with pytest.raises(RuntimeError, match="v16 predecessor is not exact and empty"):
                    await apply_office_migrations(cur)
                await cur.execute("DROP TABLE office_engine")
                await cur.execute(_V16_ENGINE_DDL)
                await cur.execute("ALTER TABLE office_engine ADD CONSTRAINT fk_office_engine_rogue FOREIGN KEY (model_ref) REFERENCES model(id)")
                with pytest.raises(RuntimeError, match="v16 predecessor is not exact and empty"):
                    await apply_office_migrations(cur)
                await cur.execute("DROP TABLE office_engine")
                await cur.execute(_V16_ENGINE_DDL)
                await cur.execute("ALTER TABLE office_engine ADD COLUMN rogue INT NULL")
                with pytest.raises(RuntimeError, match="v16 predecessor is not exact and empty"):
                    await apply_office_migrations(cur)
                await cur.execute("DROP TABLE office_engine")
                await apply_office_migrations(cur)
    client.portal.call(scenario)
