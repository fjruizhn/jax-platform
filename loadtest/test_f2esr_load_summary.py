import copy
import importlib.util
import json
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).with_name("f2esr_load_summary.py")
    spec = importlib.util.spec_from_file_location("f2esr_load_summary", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row():
    evidence = (Path(__file__).resolve().parents[1] / "docs" / "evidence" /
                "f2-e-sr-load-2026-10-03" / "http" /
                "production-baseline-8000-r1.json")
    return json.loads(evidence.read_text())


def test_first_threshold_uses_median_of_repetitions_and_ignores_warmup():
    mod = _module()
    rows = []
    for p95 in (400, 450, 900):
        rows.append({"calentamiento": {"p95_ms": 9999}, "niveles": [
            {"c": c, "p95_ms": p95 if c == 1 else 100} for c, _n in mod.LEVELS
        ]})
    assert mod._first_measured_threshold(rows) is None
    rows[0]["niveles"][0]["p95_ms"] = 600
    rows[1]["niveles"][0]["p95_ms"] = 700
    assert mod._first_measured_threshold(rows) == 1


@pytest.mark.parametrize("mutate", (
    lambda row: row["proveedor_falso_stats"].update(chat=2425),
    lambda row: row["entorno"].update(sha_jax_esperado="eca7db4"),
    lambda row: row["niveles"][0].update(rps=float("nan")),
))
def test_http_evidence_rejects_false_fake_count_prefix_sha_and_nonfinite_metric(mutate):
    mod = _module()
    row = _row(); mutate(row)
    with pytest.raises(ValueError):
        mod._check_http(row, "production-baseline", 8000, 1)


def test_micro_rejects_bad_stored_median_and_nonfinite_sample():
    mod = _module()
    sample = {"mode": "production-baseline", "repetition": 1, "chars": 8000,
        "iterations": 200, "warmup": 20, "renderer_api_version": "f2-c.renderer.2",
        "domain_spec_version": "f2-c.domain.2",
        "jax": {"sha": mod.EXPECTED["production-baseline"][0]},
        "platform": {"sha": mod.EXPECTED["production-baseline"][1]},
        "module_roots": {"platform_governed_chat": "/home/fruiz/worktrees/load-production-baseline-platform/backend/api/governed_chat.py",
            "jax_domain": "/home/fruiz/worktrees/load-production-baseline-jax/policy/governance/governed_domain.py",
            "jax_renderer": "/home/fruiz/worktrees/load-production-baseline-jax/policy/governance/governed_renderer.py",
            "jax_lifecycle": "/home/fruiz/worktrees/load-production-baseline-jax/policy/governance/output_lifecycle.py",
            "envelope_schema_versions": ["f2-c.1"], "lifecycle_api_version": "f2-d.lifecycle.2"},
        "samples": [{"wall_ms": 1.0, "cpu_ms": 2.0}] * 200,
        "median_wall_ms": 2.0, "median_cpu_ms": 2.0}
    with pytest.raises(ValueError): mod._check_micro(sample, "production-baseline", 8000, 1)
    sample["median_wall_ms"] = 1.0
    sample["samples"][0] = {"wall_ms": float("inf"), "cpu_ms": 2.0}
    with pytest.raises(ValueError): mod._check_micro(sample, "production-baseline", 8000, 1)
