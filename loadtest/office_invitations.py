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
different path.  The local benchmark process deliberately uses a documented,
high login/invitation limiter threshold so acceptance throughput is not
mislabelled as capacity after a shared bucket was spent by an earlier level.
Production guardrails remain unchanged.  Nothing in this module targets
production ports, hosts, or database names.
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


def summarize(latencies: list[float], statuses: dict[int | str, int], started: float,
              error_details: list[str] | None = None, semantic_errors: int = 0) -> dict:
    n = sum(statuses.values())
    http_ok = sum(v for k, v in statuses.items() if k in {200, 201, 204})
    return {
        "n": n, "ok": max(0, http_ok - semantic_errors),
        # A successful status is insufficient for the deliberately worst-case
        # list: an incomplete page is a failed measurement too.
        "errors": n - http_ok + semantic_errors,
        "statuses": {str(k): v for k, v in sorted(statuses.items(), key=lambda item: str(item[0]))},
        "rps": round(n / max(time.perf_counter() - started, 0.001), 2),
        "p50_ms": round(percentile(latencies, 50), 2) if latencies else None,
        "p95_ms": round(percentile(latencies, 95), 2) if latencies else None,
        "p99_ms": round(percentile(latencies, 99), 2) if latencies else None,
        "max_ms": round(max(latencies), 2) if latencies else None,
        # Bodies are only error codes from local test HTTP; bearer tokens and
        # passwords are never response fields and are never recorded here.
        "error_details": sorted(set(error_details or []))[:10],
    }


def degradation(rows: list[dict]) -> str:
    baseline = rows[0]["result"]
    for row in rows:
        r = row["result"]
        if r["errors"]:
            return f"c={row['concurrency']}: {r['errors']} request/semantic errors"
        if baseline["p95_ms"] and r["p95_ms"] and r["p95_ms"] > baseline["p95_ms"] * 3:
            return f"c={row['concurrency']}: p95 {r['p95_ms']}ms > 3x baseline {baseline['p95_ms']}ms"
    return f"not observed through c={rows[-1]['concurrency']}"


def is_loopback_host(host: str) -> bool:
    return host in {"127.0.0.1", "::1"}


def has_exact_loopback_port_binding(bindings: dict, host: str, port: str) -> bool:
    """Require Docker's bound address to equal the configured IP family."""
    # Docker inspect normally serializes IPv6 HostIp as ``::1``; accept its
    # bracketed presentation too, while retaining exact IPv4/IPv6 matching.
    def normalized(address: object) -> object:
        return address[1:-1] if isinstance(address, str) and address.startswith("[") and address.endswith("]") else address
    return any(binding.get("HostPort") == port and normalized(binding.get("HostIp")) == normalized(host)
               for values in bindings.values() if values for binding in values)


async def sequential_workers(concurrency: int, operations: int, action):
    """Run exactly ``concurrency`` workers, each performing serial work."""
    async def worker(worker_id: int):
        return [await action(worker_id, operation) for operation in range(operations)]
    return [item for row in await asyncio.gather(*(worker(worker_id) for worker_id in range(concurrency))) for item in row]


def env_or_abort() -> dict[str, str]:
    required = ("JAX_OFFICE_LOAD_DB", "JAX_DB_HOST", "JAX_DB_PORT", "JAX_DB_USER", "JAX_DB_PASSWORD",
                "JAX_REPO_PATH", "JAX_CONFIG_PATH", "OFFICE_LOAD_CONTAINER")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise SystemExit("missing required environment: " + ", ".join(missing))
    env = {key: os.environ[key] for key in required}
    if not DATABASE_RE.fullmatch(env["JAX_OFFICE_LOAD_DB"]):
        raise SystemExit("JAX_OFFICE_LOAD_DB must be a dedicated jax_memory_test_<8 hex> database")
    if not is_loopback_host(env["JAX_DB_HOST"]):
        raise SystemExit("JAX_DB_HOST must be literal loopback for the declared disposable container")
    if env["JAX_DB_PORT"] in {"3306", "3308"}:
        raise SystemExit("JAX_DB_PORT is a known production/default port; use a disposable container port")
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


def assert_disposable_container(cfg: dict[str, str]) -> None:
    """Bind the requested loopback database port to a running named container.

    A test-shaped schema name alone is not a production boundary: a production
    server could host one.  The operator must therefore declare the disposable
    MariaDB container and this check confirms that it exposes the configured
    port on loopback before SQL is opened.
    """
    try:
        state = subprocess.check_output(
            ["sudo", "-n", "docker", "inspect", "--format", "{{.State.Running}}", cfg["OFFICE_LOAD_CONTAINER"]],
            text=True, stderr=subprocess.DEVNULL).strip()
        ports = subprocess.check_output(
            ["sudo", "-n", "docker", "inspect", "--format", "{{json .NetworkSettings.Ports}}", cfg["OFFICE_LOAD_CONTAINER"]],
            text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("cannot inspect the declared disposable Office load container") from error
    if state != "true":
        raise RuntimeError("declared Office load container is not running")
    bindings = json.loads(ports)
    wanted = str(cfg["JAX_DB_PORT"])
    if not has_exact_loopback_port_binding(bindings, cfg["JAX_DB_HOST"], wanted):
        raise RuntimeError("declared container does not expose JAX_DB_HOST/JAX_DB_PORT exactly")


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
               JAX_PLATFORM_URL=f"http://127.0.0.1:{port}",
               # Configuration validates the canonical public origin even
               # though this harness never sends an email or opens that URL.
               OFFICE_INVITATION_PUBLIC_BASE_URL="https://virtualoffice.example.test",
               OFFICE_INVITATION_LIFETIME_SECONDS="600", OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS="30",
               OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT="50", OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT="50",
               OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS="120", OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS="3",
               OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS="300", OFFICE_MFA_RECENCY_SECONDS="300",
               OFFICE_MFA_RECOVERY_CODE_COUNT="3", CANARY_INTERVAL_SECONDS="0",
               JAX_USAGE_SPOOL_DIR=str(tmp / "usage"), JAX_FACET_SEAL_PATH=str(tmp / "seal"),
               JAX_KILL_SWITCH_PATH=str(tmp / "pause"), JAX_AUDIT_LOG_PATH=str(tmp / "audit.jsonl"),
               JAX_REPO_BASE=str(tmp / "repo"), JAX_ADJUNTOS_DIR=str(tmp / "adjuntos"),
               JAX_PROXY_CARRIL_RAIZ=str(tmp / "carril"), LAS_MANOS_URL="http://127.0.0.1:1",
               JACOBS_URL="http://127.0.0.1:1/jacobs", JAX_OLLAMA_URL="http://127.0.0.1:1",
               JAX_ADJUNTO_MAX_BYTES="10485760", JAX_ADJUNTO_MAX_CHARS="8000",
               JAX_ADJUNTO_MAX_PAGINAS="20", JAX_ADJUNTO_MAX_POR_MENSAJE="1",
               JAX_ADJUNTO_IMAGENES_EN_PROCESO="1", JAX_ADJUNTO_SUBIDAS_EN_PROCESO="1",
               JAX_ADJUNTO_PDF_PROCESOS="1", JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS="5",
               JAX_ADJUNTOS_TTL_HORAS="24", JAX_ADJUNTOS_CUOTA_BYTES_USUARIO="524288000",
               JAX_ADJUNTOS_DISCO_LIBRE_MINIMO_BYTES="1073741824",
               JAX_ADJUNTOS_SUBIDAS_POR_MINUTO="600", JAX_ADJUNTOS_RECHAZO_ESPERA_MS="0",
               # Isolated benchmark-only process.  This prevents its shared
               # in-memory limiter from obscuring acceptance capacity across
               # c=1/c=5/c=10; its values are emitted in the result below.
               JAX_LOGIN_RATE_IP="10000/60", JAX_LOGIN_RATE_EMAIL="10000/60",
               JAX_LOGIN_RATE_MAX_KEYS="20000")
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
    totp = pyotp.TOTP(enrollment["secret"])
    enrollment_otp = totp.now()
    confirm = await client.post("/api/auth/mfa/enrollment/confirm", json={"enrollment_token": enrollment["enrollment_token"], "otp": enrollment_otp})
    confirm.raise_for_status()
    login = await client.post("/api/auth/login", json={"email": email, "password": password})
    login.raise_for_status()
    challenge = login.json()
    # MFA deliberately rejects reuse of a TOTP counter.  Enrollment consumed
    # this counter, so wait for the next actual clock interval rather than
    # fabricating a proof or weakening the replay guard for a load test.
    await asyncio.sleep(max(.1, 30 - (time.time() % 30) + .1))
    verified = await client.post("/api/auth/mfa/login/verify", json={"challenge_token": challenge["challenge_token"], "otp": totp.now()})
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
        results = await sequential_workers(concurrency, OPERATIONS_PER_WORKER,
                                           lambda worker, operation: issue(kind, worker * OPERATIONS_PER_WORKER + operation))
        stats, latencies, errors = {}, [], []
        for response, latency in results:
            stats[response.status_code] = stats.get(response.status_code, 0) + 1
            if response.status_code == 200:
                body = response.json(); issued.append((body["id"], int(body["version"])))
                latencies.append(latency)
            else:
                errors.append(response.text[:200])
        return summarize(latencies, stats, started, errors)
    list_started = time.perf_counter()
    async def list_once():
        before = time.perf_counter()
        response = await client.get(f"/api/virtual-office/projects/{project_id}/invitations?limit=100", headers=headers)
        return response, (time.perf_counter() - before) * 1000
    lists = await sequential_workers(concurrency, OPERATIONS_PER_WORKER,
                                     lambda _worker, _operation: list_once())
    list_lat, list_errors, list_semantic_errors = [], [], 0
    list_status = {}
    for response, latency in lists:
        list_status[response.status_code] = list_status.get(response.status_code, 0) + 1
        if response.status_code == 200:
            list_lat.append(latency)
            if len(response.json().get("invitations", [])) != 100:
                list_errors.append("list did not return the seeded 100 invitations")
                list_semantic_errors += 1
        else:
            list_errors.append(response.text[:200])
    issue_result = await batch("accept")
    accept_ids = [iid for iid, _ in issued]
    tokens = await asyncio.to_thread(outbox_tokens, cfg, fernet_key, accept_ids)
    accept_started, accept_stats, accept_lat, accept_errors = time.perf_counter(), {}, [], []
    async def accept(iid: str):
        before = time.perf_counter(); response = await client.post("/api/virtual-office/invitations/accept", json={"token": tokens[iid], "password": "Loadtest-Only-Password-123!"}); return response, (time.perf_counter()-before)*1000
    accepts = await sequential_workers(concurrency, OPERATIONS_PER_WORKER,
                                       lambda worker, operation: accept(accept_ids[worker * OPERATIONS_PER_WORKER + operation]))
    for response, latency in accepts:
        accept_stats[response.status_code] = accept_stats.get(response.status_code, 0) + 1
        if response.status_code == 200: accept_lat.append(latency)
        else: accept_errors.append(response.text[:200])
    issued.clear()
    revoke_issue = await batch("revoke")
    revoke_started, revoke_stats, revoke_lat, revoke_errors = time.perf_counter(), {}, [], []
    async def revoke(iid: str, version: int):
        before = time.perf_counter(); response = await client.delete(f"/api/virtual-office/projects/{project_id}/invitations/{iid}?expected_version={version}", headers={**headers, "Idempotency-Key": str(uuid.uuid4())}); return response, (time.perf_counter()-before)*1000
    revokes = await sequential_workers(concurrency, OPERATIONS_PER_WORKER,
                                       lambda worker, operation: revoke(*issued[worker * OPERATIONS_PER_WORKER + operation]))
    for response, latency in revokes:
        revoke_stats[response.status_code] = revoke_stats.get(response.status_code, 0) + 1
        if response.status_code == 200: revoke_lat.append(latency)
        else: revoke_errors.append(response.text[:200])
    return {"concurrency": concurrency,
            "list": summarize(list_lat, list_status, list_started, list_errors, list_semantic_errors), "issue_accept": issue_result,
            "accept": summarize(accept_lat, accept_stats, accept_started, accept_errors), "issue_revoke": revoke_issue,
            "revoke": summarize(revoke_lat, revoke_stats, revoke_started, revoke_errors)}


async def seed_active_invitations(client, headers: dict, project_id: int, run_id: str) -> None:
    """Populate exactly one full list page through the real issue endpoint."""
    for index in range(100):
        response = await client.post(
            f"/api/virtual-office/projects/{project_id}/invitations",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"email": f"office-load-{run_id}-seed-{index}@example.invalid", "audience": "INTERNAL"},
        )
        if response.status_code != 200:
            raise RuntimeError(f"failed to seed active invitation page: HTTP {response.status_code} {response.text[:200]}")
    verified = await client.get(f"/api/virtual-office/projects/{project_id}/invitations?limit=100", headers=headers)
    if verified.status_code != 200 or len(verified.json().get("invitations", [])) != 100:
        raise RuntimeError("seed verification did not return exactly 100 active invitations")


async def measure(cfg: dict[str, str]) -> dict:
    await asyncio.to_thread(assert_disposable_container, cfg)
    await asyncio.to_thread(assert_disposable_runtime, cfg)
    run_id, password = uuid.uuid4().hex[:8], secrets.token_urlsafe(24)
    _, project_id = await asyncio.to_thread(provision, cfg, run_id, password)
    tmp = Path(tempfile.mkdtemp(prefix="office-invitations-load-")); port = await asyncio.to_thread(free_port)
    env, fernet_key = backend_env(cfg, tmp, port); proc = log = None
    try:
        proc, log = await asyncio.to_thread(start_backend, env, port, tmp)
        client, headers = await authenticated_client(f"http://127.0.0.1:{port}", f"office-load-admin-{run_id}@example.invalid", password)
        try:
            await seed_active_invitations(client, headers, project_id, run_id)
            rows = [await run_level(client, headers, project_id, cfg, fernet_key, run_id, c) for c in LEVELS]
        finally:
            await client.aclose()
        # Each stateful leg reports separately; the overall degradation is the
        # first operation whose p95/HTTP errors cross the declared criterion.
        gates = {name: degradation([{"concurrency": r["concurrency"], "result": r[name]} for r in rows]) for name in ("list", "issue_accept", "accept", "issue_revoke", "revoke")}
        return {"schema": "office-invitation-load/v1", "database": cfg["JAX_OFFICE_LOAD_DB"], "run_id": run_id,
                "workers_per_level": {"concurrent_users": "c", "operations_per_user": OPERATIONS_PER_WORKER},
                "seed": {"active_invitations": 100, "method": "real issue endpoint"},
                "benchmark_rate_policy": {"JAX_LOGIN_RATE_IP": "10000/60", "JAX_LOGIN_RATE_EMAIL": "10000/60", "scope": "isolated local benchmark process only"},
                "levels": rows, "degradation": gates, "notes": ["real loopback ASGI HTTP", "real auth and MFA", "outbox bearer decrypted only from the isolated disposable database", "guardrail 429 is reported separately from capacity"]}
    finally:
        if proc: await asyncio.to_thread(stop_backend, proc)
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
