"""Translation catalog for Virtual Office invitation email delivery.

Invitations are issued before a user account exists, and the invitation schema
does not store a recipient language preference.  Delivery therefore uses the
documented Spanish default until a recipient-owned preference is introduced.
"""
from __future__ import annotations

import html
import json
from functools import lru_cache
from importlib.resources import files


DEFAULT_INVITATION_EMAIL_LOCALE = "es"

_SUPPORTED_LOCALES = frozenset({"es", "en"})


@lru_cache(maxsize=len(_SUPPORTED_LOCALES))
def _email_template(locale: str) -> dict[str, str]:
    """Load a packaged translation once; deployment replaces it by restart."""
    document = json.loads(
        files("virtual_office").joinpath("locales", f"{locale}.json").read_text(encoding="utf-8"))
    template = document.get("invitation_email")
    if not isinstance(template, dict) or set(template) != {"subject", "text", "html"}:
        raise RuntimeError("office invitation email translation is invalid")
    if not all(isinstance(value, str) for value in template.values()):
        raise RuntimeError("office invitation email translation is invalid")
    return template


def render_invitation_email(*, recipient_locale: str | None, url: str,
                            expires_at: str) -> tuple[str, str, str]:
    """Render through the catalog, falling back only for a missing preference."""
    locale = recipient_locale if recipient_locale in _SUPPORTED_LOCALES else DEFAULT_INVITATION_EMAIL_LOCALE
    template = _email_template(locale)
    return (
        template["subject"],
        template["text"].format(url=url, expires_at=expires_at),
        template["html"].format(
            url=html.escape(url, quote=True),
            expires_at=html.escape(expires_at, quote=False),
        ),
    )
