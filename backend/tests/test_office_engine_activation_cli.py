"""Contract for the explicit first-engine activation command."""
from __future__ import annotations

import pytest


def test_activation_cli_requires_unique_uppercase_classifications() -> None:
    from virtual_office.engine_activation_cli import parse_classifications

    assert parse_classifications("INTERNAL_CONFIDENTIAL,PROJECT_SECRET") == frozenset({
        "INTERNAL_CONFIDENTIAL", "PROJECT_SECRET",
    })
    with pytest.raises(ValueError):
        parse_classifications("INTERNAL_CONFIDENTIAL,INTERNAL_CONFIDENTIAL")
    with pytest.raises(ValueError):
        parse_classifications("internal")
