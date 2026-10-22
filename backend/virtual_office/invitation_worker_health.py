"""Read-only invitation-worker backlog and stale-claim health probe."""
from __future__ import annotations

import asyncio
import json

from db.connection import get_pool

from .invitation_config import load_invitation_config_from_environment


async def check() -> tuple[int, dict[str, int]]:
    config = load_invitation_config_from_environment()
    if config is None:
        return 2, {"configuration": 0}
    pool = await get_pool()
    async with pool.acquire() as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(
                "SELECT "
                "SUM(state='QUEUED'),SUM(state='CLAIMED'),"
                "SUM(state='CLAIMED' AND claimed_at < DATE_SUB(UTC_TIMESTAMP(6),INTERVAL %s SECOND)) "
                "FROM office_invitation_outbox",
                (config.claim_timeout_seconds,),
            )
            queued, claimed, stale = await cursor.fetchone()
    result = {"queued": int(queued or 0), "claimed": int(claimed or 0), "stale": int(stale or 0)}
    return (2 if result["stale"] else 0), result


def main() -> int:
    code, result = asyncio.run(check())
    # Counts contain no recipient, invitation id, token, or SMTP transcript.
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
