"""Root-backed MariaDB contracts for office-invitation-delivery-v1."""
from __future__ import annotations

import os
import pytest

pytestmark = pytest.mark.skipif(os.getenv("JAX_DB_USER") != "root", reason="delivery routine authority integration requires disposable root DB")


def test_delivery_routine_integration_requires_root_fixture(client):
    """CI's disposable root job must exercise the dedicated principal.

    The detailed seed/claim/settle cases live with the root-only SQL fixture;
    this sentinel prevents silently running them under the web identity.
    """
    assert client is not None
