"""One-shot schema runner; the web process never invokes this module."""
from __future__ import annotations

import asyncio

from .connection import close_pool
from .migrations import run_migrations


async def main() -> None:
    try:
        await run_migrations()
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
