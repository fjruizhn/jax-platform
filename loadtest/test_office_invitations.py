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


def test_degradation_reports_errors_before_claiming_capacity():
    rows = [{"concurrency": 1, "result": {"errors": 0, "p95_ms": 10}},
            {"concurrency": 5, "result": {"errors": 1, "p95_ms": 11}}]
    assert "1 HTTP/client errors" in oi.degradation(rows)


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
