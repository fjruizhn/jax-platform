#!/usr/bin/env python3
"""One-time copy of working SMTP settings into Office with its own key.

The source secret is decrypted only in this process and re-encrypted with
Office's independent Fernet key. No SMTP credential is printed or logged.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import stat
import sys
from pathlib import Path

import aiomysql
from cryptography.fernet import Fernet, InvalidToken

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
import config_audit
import smtp_config


def credential(path: Path) -> dict[str, str]:
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o600):
        raise RuntimeError("SMTP import credential must be root-owned mode 0600")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.isidentifier() or key in values:
            raise RuntimeError("invalid SMTP import credential")
        values[key] = value
    required = ("JAX_DB_HOST", "JAX_DB_PORT", "JAX_DB_NAME", "JAX_DB_USER",
                "JAX_DB_PASSWORD", "FERNET_KEY")
    if any(not values.get(key) for key in required):
        raise RuntimeError("incomplete SMTP import credential")
    return values


async def connect(values: dict[str, str]):
    return await aiomysql.connect(
        host=values["JAX_DB_HOST"], port=int(values["JAX_DB_PORT"]),
        db=values["JAX_DB_NAME"], user=values["JAX_DB_USER"],
        password=values["JAX_DB_PASSWORD"], charset="utf8mb4",
        autocommit=False, connect_timeout=10,
    )


async def server_identity(connection):
    async with connection.cursor() as cur:
        await cur.execute("SELECT @@server_id,@@hostname,DATABASE()")
        return await cur.fetchone()


async def import_smtp(source: dict[str, str], office: dict[str, str], owner_email: str) -> None:
    source_id = tuple(source[key] for key in ("JAX_DB_HOST", "JAX_DB_PORT", "JAX_DB_NAME"))
    target_id = tuple(office[key] for key in ("JAX_DB_HOST", "JAX_DB_PORT", "JAX_DB_NAME"))
    if (source_id == target_id or source_id[2] != "jax_memory"
            or target_id[2] == "jax_memory" or office.get("OFFICE_DEDICATED_DB") != "1"
            or source["FERNET_KEY"] == office["FERNET_KEY"]):
        raise RuntimeError("SMTP source and Office target must have separate databases and keys")
    source_fernet = Fernet(source["FERNET_KEY"].encode("ascii"))
    office_fernet = Fernet(office["FERNET_KEY"].encode("ascii"))
    keys = smtp_config.CLAVES + smtp_config.CLAVES_OPCIONALES
    source_db = await connect(source)
    office_db = None
    try:
        office_db = await connect(office)
        source_identity = await server_identity(source_db)
        office_identity = await server_identity(office_db)
        if (source_identity[:2] == office_identity[:2]
                or str(office_identity[0]) != office.get("OFFICE_DB_SERVER_ID")
                or office_identity[1] != office.get("OFFICE_DB_HOSTNAME")
                or office_identity[2] != office["JAX_DB_NAME"]):
            raise RuntimeError("SMTP source and Office target must use distinct physical servers")
        async with source_db.cursor() as cur:
            await cur.execute(
                "SELECT config_key,config_value FROM axioma_config WHERE config_key IN ("
                + ",".join(["%s"] * len(keys)) + ")", keys)
            rows = dict(await cur.fetchall())
    finally:
        source_db.close()
        if office_db is not None:
            office_db.close()
    if not set(smtp_config.CLAVES).issubset(rows) or not rows["smtp.host"]:
        raise RuntimeError("source SMTP is not configured")
    try:
        password = source_fernet.decrypt(rows["smtp.password"].encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError) as error:
        raise RuntimeError("source SMTP password cannot be decrypted") from error
    if not password:
        raise RuntimeError("source SMTP password is empty")
    previous_key = os.environ.get("FERNET_KEY")
    try:
        os.environ["FERNET_KEY"] = source["FERNET_KEY"]
        if smtp_config.motivo_de_corrupcion(rows) is not None:
            raise RuntimeError("source SMTP configuration is invalid")
        if smtp_config.CLAVE_DESTINO_PRUEBA in rows:
            smtp_config.normalizar_destinatario(rows[smtp_config.CLAVE_DESTINO_PRUEBA])
    finally:
        if previous_key is None:
            os.environ.pop("FERNET_KEY", None)
        else:
            os.environ["FERNET_KEY"] = previous_key
    rows["smtp.password"] = office_fernet.encrypt(password.encode("utf-8")).decode("ascii")

    office_db = await connect(office)
    try:
        async with office_db.cursor() as cur:
            await cur.execute(
                "SELECT user_id,role,status FROM jax_users WHERE BINARY email=BINARY %s LIMIT 2",
                (owner_email,))
            owner_rows = await cur.fetchall()
            if len(owner_rows) != 1 or owner_rows[0][1:] != ("superadmin", "active"):
                raise RuntimeError("Office owner is not active")
            await cur.execute(
                "SELECT config_key FROM axioma_config WHERE config_key IN ("
                + ",".join(["%s"] * len(keys)) + ")", keys)
            if await cur.fetchone() is not None:
                raise RuntimeError("Office SMTP is already configured")
            await config_audit.escribir(cur, rows, int(owner_rows[0][0]), "smtp")
        await office_db.commit()
    except BaseException:
        await office_db.rollback()
        raise
    finally:
        if office_db is not None:
            office_db.close()


def main() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("SMTP import requires root")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-env", type=Path, required=True)
    parser.add_argument("--office-env", type=Path, required=True)
    parser.add_argument("--owner-email", required=True)
    args = parser.parse_args()
    asyncio.run(import_smtp(credential(args.source_env), credential(args.office_env), args.owner_email))
    print("Office SMTP settings imported with the Office key")


if __name__ == "__main__":
    main()
