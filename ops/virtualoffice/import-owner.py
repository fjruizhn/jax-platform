#!/usr/bin/env python3
"""Copy only Fernando's initial password hash into an empty Office database.

This is a one-time operator action.  No password, token, tenant project, MFA
secret, or JAX session crosses the database boundary.  The Office bootstrap
page performs its own password reauthentication before granting the first
Office role.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import stat
from pathlib import Path

import aiomysql


def _credential(path: Path) -> dict[str, str]:
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise RuntimeError("database credential must be root-owned mode 0600")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.isidentifier() or key in values:
            raise RuntimeError("invalid database credential file")
        values[key] = value
    for key in ("JAX_DB_HOST", "JAX_DB_PORT", "JAX_DB_NAME", "JAX_DB_USER", "JAX_DB_PASSWORD"):
        if not values.get(key):
            raise RuntimeError("database credential is incomplete")
    return values


async def _connect(config: dict[str, str]):
    return await aiomysql.connect(
        host=config["JAX_DB_HOST"], port=int(config["JAX_DB_PORT"]),
        user=config["JAX_DB_USER"], password=config["JAX_DB_PASSWORD"],
        db=config["JAX_DB_NAME"], charset="utf8mb4", autocommit=False,
        connect_timeout=10,
    )


async def _assert_distinct_servers(source_db, office_db, target: dict[str, str]) -> None:
    async def identity(connection):
        async with connection.cursor() as cur:
            await cur.execute("SELECT @@server_id,@@hostname,DATABASE()")
            return await cur.fetchone()
    source_identity = await identity(source_db)
    office_identity = await identity(office_db)
    if (source_identity[:2] == office_identity[:2]
            or str(office_identity[0]) != target.get("OFFICE_DB_SERVER_ID")
            or office_identity[1] != target.get("OFFICE_DB_HOSTNAME")
            or office_identity[2] != target["JAX_DB_NAME"]):
        raise RuntimeError("source and Office must be distinct physical MariaDB servers")


async def import_owner(source: dict[str, str], target: dict[str, str], email: str) -> int:
    source_id = tuple(source[key] for key in ("JAX_DB_HOST", "JAX_DB_PORT", "JAX_DB_NAME"))
    target_id = tuple(target[key] for key in ("JAX_DB_HOST", "JAX_DB_PORT", "JAX_DB_NAME"))
    if (source_id == target_id or source_id[2] != "jax_memory"
            or target_id[2] == "jax_memory" or target.get("OFFICE_DEDICATED_DB") != "1"):
        raise RuntimeError("source and Office database identities are invalid")
    source_db = await _connect(source)
    office_db = None
    try:
        office_db = await _connect(target)
        await _assert_distinct_servers(source_db, office_db, target)
        async with source_db.cursor() as cur:
            await cur.execute(
                "SELECT t.name,t.plan,t.status,u.email,u.password_hash,u.role,u.status,"
                "u.account_class,u.must_change_password FROM jax_users u "
                "JOIN jax_tenants t ON t.tenant_id=u.tenant_id "
                "WHERE BINARY u.email=BINARY %s LIMIT 2", (email,))
            rows = await cur.fetchall()
        if len(rows) != 1:
            raise RuntimeError("source owner identity is absent or ambiguous")
        tenant_name, tenant_plan, tenant_status, actual_email, password_hash, role, status, account_class, must_change = rows[0]
        if (tenant_status != "active" or role != "superadmin" or status != "active"
                or account_class != "INTERNAL" or bool(must_change) or not password_hash):
            raise RuntimeError("source owner is not an active self-controlled superadmin")
        async with office_db.cursor() as cur:
            for table in ("jax_tenants", "jax_users", "office_account_profile", "office_bootstrap_state"):
                await cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
                if await cur.fetchone() is not None:
                    raise RuntimeError("Office identity database is not empty")
            await cur.execute(
                "INSERT INTO jax_tenants (name,plan,status) VALUES (%s,%s,'active')",
                (tenant_name, tenant_plan))
            tenant_id = cur.lastrowid
            await cur.execute(
                "INSERT INTO jax_users (tenant_id,email,password_hash,role,status,account_class,must_change_password) "
                "VALUES (%s,%s,%s,'superadmin','active','INTERNAL',0)",
                (tenant_id, actual_email, password_hash))
            owner_id = cur.lastrowid
        await office_db.commit()
        return int(owner_id)
    except BaseException:
        if office_db is not None:
            await office_db.rollback()
        raise
    finally:
        source_db.close()
        if office_db is not None:
            office_db.close()


def main() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("owner import requires root to read both credentials")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-env", type=Path, required=True)
    parser.add_argument("--office-env", type=Path, required=True)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    owner_id = asyncio.run(import_owner(
        _credential(args.source_env), _credential(args.office_env), args.email))
    print(f"Office owner identity imported as user_id={owner_id}; bootstrap in the Office UI remains required")


if __name__ == "__main__":
    main()
