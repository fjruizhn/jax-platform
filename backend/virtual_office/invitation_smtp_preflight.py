"""Explicit SMTP transport preflight required before enabling the timer."""
from __future__ import annotations

import asyncio

import smtp_config
from db.connection import close_pool

from .invitation_config import load_invitation_config_from_environment


async def check() -> int:
    try:
        config = load_invitation_config_from_environment()
        if config is None:
            return 2
        settings = await smtp_config.cargar_settings()
        await asyncio.to_thread(
            smtp_config.probar_conexion,
            settings.host, settings.port, settings.encryption, settings.user, settings.password,
        )
        return 0
    finally:
        await close_pool()


def main() -> int:
    return asyncio.run(check())


if __name__ == "__main__":
    raise SystemExit(main())
