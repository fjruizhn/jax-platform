"""Fail-closed configuration for the invitation capability.

The duration is intentionally explicit.  A missing or malformed value means
that neither an issuer nor an acceptor gets a partially configured invitation
surface.  This module has no public-base-URL setting: bearer tokens are sent
only in a POST body, never composed into a URL or query string.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class InvitationConfig:
    lifetime_seconds: int

    def __post_init__(self) -> None:
        if type(self.lifetime_seconds) is not int or not 1 <= self.lifetime_seconds <= 604_800:
            raise ValueError("invitation lifetime is outside the supported range")


def load_invitation_config_from_environment() -> InvitationConfig | None:
    """Load a complete invitation policy, with no production default."""
    raw = os.getenv("OFFICE_INVITATION_LIFETIME_SECONDS")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if str(value) != raw:
        return None
    try:
        return InvitationConfig(lifetime_seconds=value)
    except ValueError:
        return None
