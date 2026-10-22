"""Repeatable, isolated HTTP load measurement for Office invitations.

This is deliberately a *local* load harness.  It starts the real ASGI app on
loopback and calls its public HTTP routes; it never uses ASGITransport or
injects an Authorization header.  The database is an explicit pre-provisioned
disposable database, named ``jax_memory_test_<8 hex>``.  It is intentionally
not cleaned up: invitation evidence is append-only, and deleting it would make
the measurement less truthful.  Drop the whole disposable database afterwards.

The only direct SQL is test-data provisioning (one Office administrator and
one Office project) and reading the encrypted local outbox payload so the
acceptance leg can use the exact bearer that the real issue endpoint emitted.
Authentication and MFA go through /api/auth/login and the MFA endpoints.

Usage (from repository root, with an already migrated disposable MariaDB):

  JAX_OFFICE_LOAD_DB=jax_memory_test_a1b2c3d4 \
  JAX_DB_HOST=127.0.0.1 JAX_DB_PORT=33992 JAX_DB_USER=root JAX_DB_PASSWORD=... \
  JAX_REPO_PATH=/path/to/jax JAX_CONFIG_PATH=/path/to/jax/config/config.toml \
  PYTHONPATH=$PWD:... python loadtest/office_invitations.py

``CARGA_RAPIDA=1`` runs only c=1 for a harness smoke check.  Results are
written below ``.cache/jax-loadtest/`` unless ``OFFICE_LOAD_RESULT`` selects a
different path.  Nothing in this module targets production ports or database
names.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DATABASE_RE = re.compile(r"jax_memory_test_[0-9a-f]{8}$")
LEVELS = [1, 5, 10]
OPERATIONS_PER_WORKER = 4
if os.environ.get("CARGA_RAPIDA"):
    LEVELS, OPERATIONS_PER_WORKER = [1], 1


def percentile(values: list[float], p: int) -> float | None:
    if not values:
        return None
    return sorted(values)[max(0, min(len(values) - 1, round(len(values) * p / 100) - 1))]


def summarize(latencies: list[float], statuses: dict[int | str, int], started: float) -> dict:
    n = sum(statuses.values())
    return {
        "n": n, "ok": sum(v for k, v in statuses.items() if k in {200, 201, 204}),
        "errors": n - sum(v for k, v in statuses.items() if k in {200, 201, 204}),
        "statuses": {str(k): v for k, v in sorted(statuses.items(), key=lambda item: str(item[0]))},
        "rps": round(n / max(time.perf_counter() - started, 0.001), 2),
        "p50_ms": round(percentile(latencies, 50), 2) if latencies else None,
        "p95_ms": round(percentile(latencies, 95), 2) if latencies else None,
        "p99_ms": round(percentile(latencies, 99), 2) if latencies else None,
        "max_ms": round(max(latencies), 2) if latencies else None,
    }


def degradation(rows: list[dict]) -> str:
    baseline = rows[0]["result"]
    for row in rows:
        r = row["result"]
        if r["errors"]:
            return f"c={row['concurrency']}: {r['errors']} HTTP/client errors"
        if baseline["p95_ms"] and r["p95_ms"] and r["p95_ms"] > baseline["p95_ms"] * 3:
            return f"c={row['concurrency']}: p95 {r['p95_ms']}ms > 3x baseline {baseline['p95_ms']}ms"
    return f"not observed through c={rows[-1]['concurrency']}"


def env_or_abort() -> dict[str, str]:
    required = ("JAX_OFFICE_LOAD_DB", "JAX_DB_HOST", "JAX_DB_PORT", "JAX_DB_USER", "JAX_DB_PASSWORD",
                "JAX_REPO_PATH", "JAX_CONFIG_PATH")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise SystemExit("missing required environment: " + ", ".join(missing))
    env = {key: os.environ[key] for key in required}
    if not DATABASE_RE.fullmatch(env["JAX_OFFICE_LOAD_DB"]):
        raise SystemExit("JAX_OFFICE_LOAD_DB must be a dedicated jax_memory_test_<8 hex> database")
    return env


def connection(cfg: dict[str, str]):
    return pymysql.connect(host=cfg["JAX_DB_HOST"], port=int(cfg["JAX_DB_PORT"]), user=cfg["JAX_DB_USER"],
                           password=cfg["JAX_DB_PASSWORD"], database=cfg["JAX_OFFICE_LOAD_DB"],
                           charset="utf8mb4", autocommit=False, connect_timeout=10)


def assert_disposable_runtime(cfg: dict[str, str]) -> None:
    conn = connection(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DATABASE()")
            if cur.fetchone()[0] != cfg["JAX_OFFICE_LOAD_DB"]:
                raise RuntimeError("database identity mismatch; aborting")
            cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='office_invitation'")
            if cur.fetchone() is None:
                raise RuntimeError("Office migration is absent; provision the disposable database first")
    finally:
        conn.close()


def provision(cfg: dict[str, str], run_id: str, password: str) -> tuple[int, int]:
    """Create only load-marked state; credentials are then exercised over HTTP."""
    import bcrypt
    email = f"office-load-admin-{run_id}@example.invalid"
    conn = connection(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO jax_users (tenant_id,email,password_hash,role,status,token_version,account_class) "
                        "VALUES (1,%s,%s,'operator','active',0,'OFFICE_INTERNAL')",
                        (email, bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()))
            user_id = int(cur.lastrowid)
            cur.execute("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'OFFICE_SUPERUSER','ACTIVE')", (user_id,))
            cur.execute("INSERT INTO projects (project_uuid,name,status) VALUES (UUID(),%s,'active')", (f"office-load-{run_id}",))
            project_id = int(cur.lastrowid)
            cur.execute("INSERT INTO jax_project_scope (project_id,tenant_id,authorization_namespace,status,created_at,created_by,updated_at) "
                        "VALUES (%s,1,'OFFICE_CONFIDENTIAL','ACTIVE',UTC_TIMESTAMP(6),'loadtest',UTC_TIMESTAMP(6))", (project_id,))
            cur.execute("INSERT INTO office_project_grant (project_id,user_id,grant_role,audience,status,granted_by) "
                        "VALUES (%s,%s,'PROJECT_ADMIN','INTERNAL','ACTIVE',%s)", (project_id, user_id, user_id))
        conn.commit()
        return user_id, project_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def backend_env(cfg: dict[str, str], tmp: Path, port: int) -> tuple[dict[str, str], bytes]:
    env = dict(os.environ)
    env.update(cfg, JAX_DB_NAME=cfg["JAX_OFFICE_LOAD_DB"], JAX_JWT_SECRET=secrets.token_urlsafe(48),
               FERNET_KEY=base64.urlsafe_b64encode(os.urandom(32)).decode(),
               JAX_PLATFORM_URL=f"http://127.0.0.1:{port}", OFFICE_INVITATION_PUBLIC_BASE_URL="http://127.0.0.1:1",
               OFFICE_INVITATION_LIFETIME_SECONDS="600", OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS="30",
               OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT="50", OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT="50",
               OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS="120", OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS="3",
               OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS="300", OFFICE_MFA_RECENCY_SECONDS="300",
               OFFICE_MFA_RECOVERY_CODE_COUNT="3", CANARY_INTERVAL_SECONDS="0",
               JAX_USAGE_SPOOL_DIR=str(tmp / "usage"), JAX_FACET_SEAL_PATH=str(tmp / "seal"),
               JAX_KILL_SWITCH_PATH=str(tmp / "pause"), JAX_AUDIT_LOG_PATH=str(tmp / "audit.jsonl"),
               JAX_REPO_BASE=str(tmp / "repo"), JAX_ADJUNTOS_DIR=str(tmp / "adjuntos"),
               JAX_PROXY_CARRIL_RAIZ=str(tmp / "carril"), LAS_MANOS_URL="http://127.0.0.1:1",
               JACOBS_URL="http://127.0.0.1:1/jacobs")
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND), os.getenv("PYTHONPATH", "")])
    return env, env["FERNET_KEY"].encode()


def start_backend(env: dict[str, str], port: int, tmp: Path):
    log = open(tmp / "backend.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                            cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError((tmp / "backend.log").read_text()[-3000:])
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return proc, log
        time.sleep(.1)
    proc.kill()
    raise RuntimeError("local ASGI app did not start")


def stop_backend(proc) -> None:
    try: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except ProcessLookupError: return
    try: proc.wait(timeout=10)
    except subprocess.TimeoutExpired: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


async def authenticated_client(base: str, email: str, password: str):
    import httpx
    import pyotp
    client = httpx.AsyncClient(base_url=base, timeout=30, follow_redirects=False)
    login = await client.post("/api/auth/login", json={"email": email, "password": password})
    login.raise_for_status()
    access = login.json()["access_token"]
    enroll = await client.post("/api/auth/mfa/enrollment/start", json={"password": password}, headers={"Authorization": f"Bearer {access}"})
    enroll.raise_for_status()
    enrollment = enroll.json()
    confirm = await client.post("/api/auth/mfa/enrollment/confirm", json={"enrollment_token": enrollment["enrollment_token"], "otp": pyotp.TOTP(enrollment["secret"]).now()})
    confirm.raise_for_status()
    login = await client.post("/api/auth/login", json={"email": email, "password": password})
    login.raise_for_status()
    challenge = login.json()
    verified = await client.post("/api/auth/mfa/login/verify", json={"challenge_token": challenge["challenge_token"], "otp": pyotp.TOTP(enrollment["secret"]).now()})
    verified.raise_for_status()
    return client, {"Authorization": f"Bearer {verified.json()['access_token']}"}


def outbox_tokens(cfg: dict[str, str], fernet_key: bytes, ids: list[str]) -> dict[str, str]:
    from cryptography.fernet import Fernet
    if not ids: return {}
    conn = connection(cfg)
    try:
        marks = ",".join(["%s"] * len(ids))
        with conn.cursor() as cur:
            cur.execute(f"SELECT invitation_id,payload_ciphertext FROM office_invitation_outbox WHERE invitation_id IN ({marks})", ids)
            rows = cur.fetchall()
        return {str(invitation_id): json.loads(Fernet(fernet_key).decrypt(bytes(payload)).decode())["token"] for invitation_id, payload in rows}
    finally:
        conn.close()


async def run_level(client, headers: dict, project_id: int, cfg: dict, fernet_key: bytes, run_id: str, concurrency: int) -> dict:
    import httpx
    issued: list[tuple[str, int]] = []
    async def issue(kind: str, index: int):
        key = str(uuid.uuid4())
        started = time.perf_counter()
        response = await client.post(f"/api/virtual-office/projects/{project_id}/invitations", headers={**headers, "Idempotency-Key": key}, json={"email": f"office-load-{run_id}-{kind}-{concurrency}-{index}@example.invalid", "audience": "INTERNAL"})
        return response, (time.perf_counter() - started) * 1000
    async def batch(kind: str):
        started = time.perf_counter()
        results = await asyncio.gather(*(issue(kind, n) for n in range(concurrency * OPERATIONS_PER_WORKER)))
        stats, latencies = {}, []
        for response, latency in results:
            stats[response.status_code] = stats.get(response.status_code, 0) + 1
            if response.status_code == 200:
                body = response.json(); issued.append((body["id"], int(body["version"])))
                latencies.append(latency)
        return summarize(latencies, stats, started)
    list_started = time.perf_counter()
    async def list_once():
        before = time.perf_counter()
        response = await client.get(f"/api/virtual-office/projects/{project_id}/invitations?limit=100", headers=headers)
        return response, (time.perf_counter() - before) * 1000
    lists = await asyncio.gather(*(list_once() for _ in range(concurrency * OPERATIONS_PER_WORKER)))
    list_lat = []
    list_status = {}
    for response, latency in lists:
        list_status[response.status_code] = list_status.get(response.status_code, 0) + 1
        if response.status_code == 200:
            list_lat.append(latency)
    issue_result = await batch("accept")
    accept_ids = [iid for iid, _ in issued]
    tokens = outbox_tokens(cfg, fernet_key, accept_ids)
    accept_started, accept_stats, accept_lat = time.perf_counter(), {}, []
    async def accept(iid: str):
        before = time.perf_counter(); response = await client.post("/api/virtual-office/invitations/accept", json={"token": tokens[iid], "password": "Loadtest-Only-Password-123!"}); return response, (time.perf_counter()-before)*1000
    for response, latency in await asyncio.gather(*(accept(iid) for iid in accept_ids)):
        accept_stats[response.status_code] = accept_stats.get(response.status_code, 0) + 1
        if response.status_code == 200: accept_lat.append(latency)
    issued.clear()
    revoke_issue = await batch("revoke")
    revoke_started, revoke_stats, revoke_lat = time.perf_counter(), {}, []
    async def revoke(iid: str, version: int):
        before = time.perf_counter(); response = await client.delete(f"/api/virtual-office/projects/{project_id}/invitations/{iid}?expected_version={version}", headers={**headers, "Idempotency-Key": str(uuid.uuid4())}); return response, (time.perf_counter()-before)*1000
    for response, latency in await asyncio.gather(*(revoke(iid, version) for iid, version in issued)):
        revoke_stats[response.status_code] = revoke_stats.get(response.status_code, 0) + 1
        if response.status_code == 200: revoke_lat.append(latency)
    return {"concurrency": concurrency, "list": summarize(list_lat, list_status, list_started), "issue_accept": issue_result,
            "accept": summarize(accept_lat, accept_stats, accept_started), "issue_revoke": revoke_issue,
            "revoke": summarize(revoke_lat, revoke_stats, revoke_started)}


async def measure(cfg: dict[str, str]) -> dict:
    assert_disposable_runtime(cfg)
    run_id, password = uuid.uuid4().hex[:8], secrets.token_urlsafe(24)
    _, project_id = provision(cfg, run_id, password)
    tmp = Path(tempfile.mkdtemp(prefix="office-invitations-load-")); port = free_port()
    env, fernet_key = backend_env(cfg, tmp, port); proc = log = None
    try:
        proc, log = start_backend(env, port, tmp)
        client, headers = await authenticated_client(f"http://127.0.0.1:{port}", f"office-load-admin-{run_id}@example.invalid", password)
        try:
            rows = [await run_level(client, headers, project_id, cfg, fernet_key, run_id, c) for c in LEVELS]
        finally:
            await client.aclose()
        # Each stateful leg reports separately; the overall degradation is the
        # first operation whose p95/HTTP errors cross the declared criterion.
        gates = {name: degradation([{"concurrency": r["concurrency"], "result": r[name]} for r in rows]) for name in ("list", "issue_accept", "accept", "issue_revoke", "revoke")}
        return {"schema": "office-invitation-load/v1", "database": cfg["JAX_OFFICE_LOAD_DB"], "run_id": run_id,
                "levels": rows, "degradation": gates, "notes": ["real loopback ASGI HTTP", "real auth and MFA", "outbox bearer decrypted only from the isolated disposable database"]}
    finally:
        if proc: stop_backend(proc)
        if log: log.close()


def main() -> int:
    cfg = env_or_abort()
    result = asyncio.run(measure(cfg))
    target = Path(os.getenv("OFFICE_LOAD_RESULT", ROOT / ".cache/jax-loadtest/office-invitations.json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__": raise SystemExit(main())
