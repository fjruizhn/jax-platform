"""Fail-closed configuration for the invitation capability."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class InvitationConfig:
    lifetime_seconds: int
    claim_timeout_seconds: int
    stale_claim_batch_limit: int
    expiry_sweep_batch_limit: int
    public_base_url: str
    delivery_batch_limit: int = 25

    def __post_init__(self) -> None:
        if type(self.lifetime_seconds) is not int or not 1 <= self.lifetime_seconds <= 604_800:
            raise ValueError("invitation lifetime is outside the supported range")
        if type(self.claim_timeout_seconds) is not int or not 1 <= self.claim_timeout_seconds <= 3_600:
            raise ValueError("invitation claim timeout is outside the supported range")
        if type(self.stale_claim_batch_limit) is not int or not 1 <= self.stale_claim_batch_limit <= 1_000:
            raise ValueError("invitation stale claim batch limit is outside the supported range")
        # The SQL SECURITY DEFINER routine rejects values above 100.  Keep the
        # runtime contract identical, so a valid config can never make each
        # worker one-shot fail at privilege-protected procedure input.
        if type(self.expiry_sweep_batch_limit) is not int or not 1 <= self.expiry_sweep_batch_limit <= 100:
            raise ValueError("invitation expiry sweep batch limit is outside the supported range")
        # SMTP timeouts apply to individual protocol exchanges.  Keep this
        # bound deliberately small under the systemd one-shot deadline.
        if type(self.delivery_batch_limit) is not int or not 1 <= self.delivery_batch_limit <= 25:
            raise ValueError("invitation delivery batch limit is outside the supported range")
        if not _is_public_https_origin(self.public_base_url):
            raise ValueError("invitation public base URL must be a canonical HTTPS origin")


def _is_public_https_origin(value: str) -> bool:
    """Accept only a canonical HTTPS origin, never a route or credentials."""
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 2_048:
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.hostname is not None
        and parsed.hostname.isascii()
        and parsed.username is None
        and parsed.password is None
        and parsed.path == ""
        and not parsed.query
        and not parsed.fragment
        and (port is None or 1 <= port <= 65_535)
    )


def load_invitation_config_from_environment() -> InvitationConfig | None:
    """Load a complete invitation policy, with no production default."""
    raw_lifetime = os.getenv("OFFICE_INVITATION_LIFETIME_SECONDS")
    raw_claim_timeout = os.getenv("OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS")
    raw_stale_claim_batch_limit = os.getenv("OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT")
    raw_expiry_sweep_batch_limit = os.getenv("OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT")
    raw_delivery_batch_limit = os.getenv("OFFICE_INVITATION_DELIVERY_BATCH_LIMIT")
    public_base_url = os.getenv("OFFICE_INVITATION_PUBLIC_BASE_URL")
    if (raw_lifetime is None or raw_claim_timeout is None or raw_stale_claim_batch_limit is None
            or raw_expiry_sweep_batch_limit is None or raw_delivery_batch_limit is None
            or public_base_url is None):
        return None
    try:
        lifetime = int(raw_lifetime)
        claim_timeout = int(raw_claim_timeout)
        stale_claim_batch_limit = int(raw_stale_claim_batch_limit)
        expiry_sweep_batch_limit = int(raw_expiry_sweep_batch_limit)
        delivery_batch_limit = int(raw_delivery_batch_limit)
    except (TypeError, ValueError):
        return None
    if (str(lifetime) != raw_lifetime or str(claim_timeout) != raw_claim_timeout
            or str(stale_claim_batch_limit) != raw_stale_claim_batch_limit
            or str(expiry_sweep_batch_limit) != raw_expiry_sweep_batch_limit
            or str(delivery_batch_limit) != raw_delivery_batch_limit):
        return None
    try:
        config = InvitationConfig(
            lifetime_seconds=lifetime,
            claim_timeout_seconds=claim_timeout,
            stale_claim_batch_limit=stale_claim_batch_limit,
            expiry_sweep_batch_limit=expiry_sweep_batch_limit,
            public_base_url=public_base_url,
            delivery_batch_limit=delivery_batch_limit,
        )
    except ValueError:
        return None
    if os.getenv("JAX_ENVIRONMENT", "").strip().lower() == "production":
        # The browser layer establishes OFFICE_PUBLIC_ORIGIN through its host
        # binding.  The delivery worker receives the same root-owned value and
        # refuses to create a bearer link unless its base is byte-for-byte the
        # canonical production origin.
        try:
            from auth.browser_security import BrowserSecurityConfigError, _validate_origin

            _office_host, office_origin = _validate_origin(
                os.getenv("OFFICE_PUBLIC_ORIGIN", ""), production=True)
        except (BrowserSecurityConfigError, ValueError):
            return None
        if config.public_base_url != office_origin:
            return None
    return config
