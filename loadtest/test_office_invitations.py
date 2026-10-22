"""Pure safeguards for the Office invitation load harness."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import office_invitations as oi  # noqa: E402


def test_percentile_and_empty_summary_are_honest():
    assert oi.percentile([], 95) is None
    assert oi.percentile([float(n) for n in range(1, 101)], 95) == 95
    assert oi.summarize([], {}, 0)["p95_ms"] is None


def test_summary_uses_the_clock_when_its_operation_finishes(monkeypatch):
    monkeypatch.setattr(oi.time, "perf_counter", lambda: 104.0)
    assert oi.summarize([2.0] * 4, {200: 4}, 100.0)["rps"] == 1.0


def test_semantic_list_error_is_counted_even_when_http_status_is_200():
    result = oi.summarize([3.0], {200: 1}, 0, ["list did not return the seeded 100 invitations"], 1)
    assert result["ok"] == 0
    assert result["errors"] == 1


def test_degradation_reports_errors_before_claiming_capacity():
    rows = [{"concurrency": 1, "result": {"errors": 0, "p95_ms": 10}},
            {"concurrency": 5, "result": {"errors": 1, "p95_ms": 11}}]
    assert "1 request/semantic errors" in oi.degradation(rows)


@pytest.mark.parametrize("name", ["jax_memory", "jax_memory_test", "jax_memory_test_ABCDEF12", "other_a1b2c3d4"])
def test_database_pattern_rejects_non_disposable_names(name):
    assert oi.DATABASE_RE.fullmatch(name) is None


def test_database_pattern_accepts_only_explicit_disposable_database():
    assert oi.DATABASE_RE.fullmatch("jax_memory_test_a1b2c3d4") is not None


def test_known_production_ports_are_rejected_before_any_connection(monkeypatch):
    values = {
        "JAX_OFFICE_LOAD_DB": "jax_memory_test_a1b2c3d4", "JAX_DB_HOST": "127.0.0.1",
        "JAX_DB_PORT": "3308", "JAX_DB_USER": "u", "JAX_DB_PASSWORD": "p",
        "JAX_REPO_PATH": "/tmp/jax", "JAX_CONFIG_PATH": "/tmp/config", "OFFICE_LOAD_CONTAINER": "db",
    }
    for key, value in values.items(): monkeypatch.setenv(key, value)
    with pytest.raises(SystemExit, match="known production"):
        oi.env_or_abort()


def test_remote_host_is_rejected_even_when_schema_and_port_look_disposable(monkeypatch):
    values = {
        "JAX_OFFICE_LOAD_DB": "jax_memory_test_a1b2c3d4", "JAX_DB_HOST": "db.prod.example",
        "JAX_DB_PORT": "33991", "JAX_DB_USER": "u", "JAX_DB_PASSWORD": "p",
        "JAX_REPO_PATH": "/tmp/jax", "JAX_CONFIG_PATH": "/tmp/config", "OFFICE_LOAD_CONTAINER": "db",
    }
    for key, value in values.items(): monkeypatch.setenv(key, value)
    with pytest.raises(SystemExit, match="literal loopback"):
        oi.env_or_abort()


def test_container_binding_requires_the_configured_loopback_address_family_and_port():
    bindings = {"3306/tcp": [{"HostIp": "127.0.0.1", "HostPort": "33991"}]}
    assert oi.has_exact_loopback_port_binding(bindings, "127.0.0.1", "33991")
    assert not oi.has_exact_loopback_port_binding(bindings, "::1", "33991")
    assert not oi.has_exact_loopback_port_binding(bindings, "127.0.0.1", "3308")
    assert not oi.has_exact_loopback_port_binding({"3306/tcp": [{"HostIp": "0.0.0.0", "HostPort": "33991"}]}, "127.0.0.1", "33991")


def test_container_binding_accepts_docker_ipv6_loopback_serialization():
    assert oi.has_exact_loopback_port_binding(
        {"3306/tcp": [{"HostIp": "::1", "HostPort": "33991"}]}, "::1", "33991")
    assert oi.has_exact_loopback_port_binding(
        {"3306/tcp": [{"HostIp": "[::1]", "HostPort": "33991"}]}, "::1", "33991")


def test_clean_git_revision_requires_a_full_sha_and_no_changes(monkeypatch, tmp_path):
    def clean(command, **kwargs):
        return "a" * 40 + "\n" if command[1] == "rev-parse" else ""
    monkeypatch.setattr(oi.subprocess, "check_output", clean)
    assert oi.clean_git_revision(tmp_path, "fixture") == "a" * 40


def test_clean_git_revision_rejects_untracked_or_modified_sources(monkeypatch, tmp_path):
    def dirty(command, **kwargs):
        return "a" * 40 + "\n" if command[1] == "rev-parse" else "?? generated.json\n"
    monkeypatch.setattr(oi.subprocess, "check_output", dirty)
    with pytest.raises(RuntimeError, match="clean fixture git tree"):
        oi.clean_git_revision(tmp_path, "fixture")


def test_benchmark_process_raises_both_login_and_invitation_accept_buckets(tmp_path):
    cfg = {
        "JAX_OFFICE_LOAD_DB": "jax_memory_test_a1b2c3d4", "JAX_DB_HOST": "127.0.0.1",
        "JAX_DB_PORT": "33991", "JAX_DB_USER": "u", "JAX_DB_PASSWORD": "p",
        "JAX_REPO_PATH": "/tmp/jax", "JAX_CONFIG_PATH": "/tmp/config", "OFFICE_LOAD_CONTAINER": "db",
    }
    env, _ = oi.backend_env(cfg, tmp_path, 54321)
    assert env["JAX_LOGIN_RATE_IP"] == "10000/60"
    assert env["JAX_LOGIN_RATE_EMAIL"] == "10000/60"
    assert env["JAX_OFFICE_INVITATION_ACCEPT_RATE_IP"] == "10000/60"
    assert env["JAX_OFFICE_INVITATION_ACCEPT_RATE_TOKEN"] == "10000/60"
    assert env["JAX_OFFICE_INVITATION_ACCEPT_RATE_MAX_KEYS"] == "20000"


def test_sequential_workers_never_overlap_operations_within_a_user():
    active, peak, order = {}, [0], []
    async def action(worker, operation):
        active[worker] = active.get(worker, 0) + 1
        peak[0] = max(peak[0], active[worker])
        order.append((worker, operation))
        await __import__("asyncio").sleep(0)
        active[worker] -= 1
        return worker, operation
    result = __import__("asyncio").run(oi.sequential_workers(3, 4, action))
    assert result == [(worker, operation) for worker in range(3) for operation in range(4)]
    assert set(order) == set(result)
    assert peak == [1]
