"""Bootstrap a fresh, separate Office MariaDB before starting its API."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from db.connection import close_pool, get_pool
from db.migrations import run_migrations
from virtual_office.db_identity import require_dedicated_db


# Office keeps the projects table shape that its proven project authority
# modules use.  It owns this copy in its own database; no rows are imported.
_PROJECTS_DDL = """CREATE TABLE projects (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  project_uuid CHAR(36) NOT NULL UNIQUE,
  name VARCHAR(255) NOT NULL,
  description TEXT NULL,
  status ENUM('planning','active','paused','completed','archived','hidden','disabled') DEFAULT 'planning',
  start_date DATE NULL,
  target_date DATE NULL,
  completion_date DATE NULL,
  github_repo VARCHAR(255) NULL,
  created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_status(status),
  FULLTEXT KEY ft_project_name(name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""

_PROJECTS_COLUMNS = (
    "id", "project_uuid", "name", "description", "status", "start_date",
    "target_date", "completion_date", "github_repo", "created_at", "updated_at",
)


async def _projects_table() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                "AND TABLE_NAME='projects'")
            if await cur.fetchone() is None:
                await cur.execute(_PROJECTS_DDL)
            await cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                "AND TABLE_NAME='projects' ORDER BY ORDINAL_POSITION")
            if tuple(row[0] for row in await cur.fetchall()) != _PROJECTS_COLUMNS:
                raise RuntimeError("Office projects schema is incompatible")


async def migrate() -> None:
    require_dedicated_db()
    jax_root = Path(os.environ["JAX_REPO_PATH"]).resolve(strict=True)
    if not jax_root.is_dir() or not (jax_root / "jax" / "memory").is_dir():
        raise RuntimeError("paired JAX project authority source is absent")
    sys.path.insert(0, str(jax_root))
    try:
        await _projects_table()
        from jacobs import store
        await store.init_tables()
        await run_migrations()
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(migrate())
