"""GET /api/admin/keys used to issue one SELECT per provider for the stored
API key and another SELECT per provider for the active facet model — an N+1
query pattern that scales linearly with len(PROVIDERS). This pins the fix:
those reads must be batched into O(1) queries (one IN (...) per table),
regardless of how many providers exist.
"""
import aiomysql
import pytest

from auth.jwt import create_access_token

USER_ID = "test-admin-keys-user"
TENANT_ID = "test-admin-keys-tenant"


def _superadmin_headers():
    token = create_access_token(USER_ID, TENANT_ID, "superadmin")
    return {"Authorization": f"Bearer {token}"}


class _QueryCounter:
    """Wraps aiomysql.Cursor.execute to count SELECTs against the two
    tables list_keys reads from, without touching real query execution."""

    def __init__(self):
        self.key_selects = 0
        self.model_selects = 0
        self._original = aiomysql.cursors.Cursor.execute

    def __enter__(self):
        original = self._original
        counter = self

        async def wrapped(cursor_self, query, args=None):
            normalized = " ".join(query.split())
            if normalized.strip().upper().startswith("SELECT"):
                if "FROM user_api_keys" in normalized:
                    counter.key_selects += 1
                if "FROM facet_models" in normalized:
                    counter.model_selects += 1
            return await original(cursor_self, query, args)

        aiomysql.cursors.Cursor.execute = wrapped
        return self

    def __exit__(self, *exc):
        aiomysql.cursors.Cursor.execute = self._original


def test_list_keys_reads_are_not_n_plus_one(client):
    """Reproduces the N+1: with 5 providers configured, the unmodified
    endpoint issues 5 SELECTs against user_api_keys and 5 against
    facet_models (one per provider) instead of one batched IN (...) query
    per table."""
    from api.admin.keys import PROVIDERS

    with _QueryCounter() as counter:
        resp = client.get("/api/admin/keys", headers=_superadmin_headers())

    assert resp.status_code == 200, resp.text
    assert len(resp.json()["providers"]) == len(PROVIDERS)

    assert counter.key_selects <= 1, (
        f"expected O(1) SELECTs against user_api_keys, got {counter.key_selects} "
        f"for {len(PROVIDERS)} providers (N+1 pattern)"
    )
    assert counter.model_selects <= 1, (
        f"expected O(1) SELECTs against facet_models, got {counter.model_selects} "
        f"for {len(PROVIDERS)} providers (N+1 pattern)"
    )
