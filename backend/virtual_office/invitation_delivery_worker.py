"""One-shot, bounded invitation delivery worker for systemd timers.

This module is intentionally separate from ASGI startup.  It prints neither
recipients nor invitation identifiers nor bearer material; systemd receives
only an exit status suitable for OnFailure alerting.
"""
from __future__ import annotations

import asyncio
import logging

from .invitations import InvitationDeliveryMaintenanceFailure, InvitationError, drain_invitation_deliveries


logger = logging.getLogger(__name__)


async def run_once() -> int:
    await drain_invitation_deliveries()
    return 0


def main() -> int:
    try:
        return asyncio.run(run_once())
    except (InvitationDeliveryMaintenanceFailure, InvitationError) as error:
        logger.error("office invitation delivery worker failed class=%s code=%s", type(error).__name__, error.code)
        return 1
    except BaseException as error:
        # Do not serialize exceptions: SMTP libraries can include recipient
        # addresses and this process has transient bearer plaintext in memory.
        logger.error("office invitation delivery worker failed class=%s code=unexpected", type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
