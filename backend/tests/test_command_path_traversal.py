"""Path traversal in GET /api/command/{task_id}.

`task_id` is only ever a `str(uuid.uuid4())` in the legitimate flow (produced
server-side by POST /api/command). The GET handler never checks that shape
before using `task_id` to build a filesystem path, so a crafted value can
make `result_file` resolve outside MISSIONS_DIR and its contents get read
back verbatim.

Note on how this is tested: Starlette's router decodes percent-encoded
sequences (including %2F) before matching, and `{task_id}` is a single path
segment (regex `[^/]+`), so a real "/" landing in the decoded path never
matches the route at all -- it 404s in the router before `get_command_result`
ever runs. `test_encoded_slash_404s_at_router_not_the_handler` proves that
first, then the actual leak is demonstrated by calling the handler directly
(same production code, same MISSIONS_DIR, real files on disk) -- exactly the
fallback the task brief calls for when routing makes an end-to-end HTTP
payload impractical.
"""
import uuid

import pytest
from fastapi import HTTPException

from api.command import MISSIONS_DIR, get_command_result
from auth.jwt import create_access_token
from auth.models import AuthUser

SENTINEL = "TOP-SECRET-OUTSIDE-MISSIONS-DIR-1a2b3c"


def test_encoded_slash_404s_at_router_not_the_handler(client):
    """Confirms %2F never reaches the handler -- justifies the direct-call test below."""
    token = create_access_token("attacker", "1", "operator")
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.get(
        "/api/command/..%2F..%2F..%2Fetc%2Fpasswd", headers=headers
    )

    assert resp.status_code == 404


@pytest.fixture
def outside_secret():
    """A file outside MISSIONS_DIR, plus the pivot dir needed to reach it.

    MISSIONS_DIR is a hardcoded module-level constant (not injectable), so
    this creates real entries under it and cleans them up afterward -- same
    real directory the production code already reads and writes.
    """
    pivot_dir = MISSIONS_DIR / "web-task-pivot"
    outside_dir = MISSIONS_DIR.parent / "outside-secret"
    secret_file = outside_dir / "leaked_result.md"

    pivot_dir.mkdir(parents=True, exist_ok=True)
    outside_dir.mkdir(parents=True, exist_ok=True)
    secret_file.write_text(SENTINEL)

    yield "pivot/../../outside-secret/leaked"

    secret_file.unlink(missing_ok=True)
    outside_dir.rmdir()
    pivot_dir.rmdir()


async def test_path_traversal_does_not_leak_file_outside_missions_dir(outside_secret):
    user = AuthUser(user_id="attacker", tenant_id="1", role="operator")

    try:
        result = await get_command_result(task_id=outside_secret, user=user)
    except HTTPException as exc:
        assert exc.status_code == 400
        return

    assert SENTINEL not in result.get("result", "")


async def test_truly_unknown_uuid_404s_instead_of_claiming_running(client):
    """Un task_id que nunca se creó (sin owner file) ya no reporta "running"
    para siempre -- ver test_command_ownership.py para el caso real: un task
    propio reporta running normalmente antes de completarse."""
    token = create_access_token("real-user", "1", "operator")
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.get(f"/api/command/{uuid.uuid4()}", headers=headers)

    assert resp.status_code == 404
