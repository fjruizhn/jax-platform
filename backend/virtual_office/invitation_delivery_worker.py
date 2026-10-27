"""One-shot, bounded invitation delivery worker for systemd timers.

This module is intentionally separate from ASGI startup.  It prints neither
recipients nor invitation identifiers nor bearer material; systemd receives
only an exit status suitable for OnFailure alerting.
"""
from __future__ import annotations

import asyncio
import logging

from .invitations import InvitationDeliveryMaintenanceFailure, InvitationError, drain_invitation_deliveries
from .account_invitations import AccountInvitationError, drain_account_invitation_deliveries


logger = logging.getLogger(__name__)


async def run_once() -> int:
    # A timer invocation is a complete process boundary.  Close the shared
    # pool even on the failure path so repeated invocations cannot retain
    # sockets until systemd reaps the process.
    from db.connection import close_pool

    try:
        await drain_invitation_deliveries()
        # Both queues use the one explicitly provisioned worker principal, but
        # remain distinct lifecycles.  A human-account mail can never be
        # mistaken for a project grant merely because the timer is shared.
        import smtp_config
        from .invitation_config import load_invitation_config_from_environment
        config = load_invitation_config_from_environment()
        if config is None:
            raise AccountInvitationError("office_account_invitation_transport_unavailable", status_code=503)
        await drain_account_invitation_deliveries(config=config, smtp_settings=await smtp_config.cargar_settings())
        return 0
    finally:
        await close_pool()


def main() -> int:
    try:
        return asyncio.run(run_once())
    except (InvitationDeliveryMaintenanceFailure, InvitationError, AccountInvitationError) as error:
        logger.error("office invitation delivery worker failed class=%s code=%s", type(error).__name__, error.code)
        return 1
    except Exception as error:  # fail-soft: emit only the exception class and terminate nonzero without exposing invitation material
        # Do not serialize exceptions: SMTP libraries can include recipient
        # addresses and this process has transient bearer plaintext in memory.
        logger.error("office invitation delivery worker failed class=%s code=unexpected", type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
