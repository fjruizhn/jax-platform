"""Fail-closed summary for the exact 18-cell F2-E-SR HTTP load matrix."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics


HARNESS_SHA = "e2f066007bafc4dc502fceeb7a849dc979d24ba7"
HTTP_LABELS = ("production-baseline", "f2d", "sr")
MICRO_LABELS = {"production-baseline": "production-baseline", "f2d": "f2d", "sr": "f2esr"}
SIZES, REPS, LEVELS = (8000, 16000), (1, 2, 3), ((1, 100), (5, 150), (10, 200), (25, 375), (50, 600))
EXPECTED = {
    "production-baseline": ("eca7db42d529fc54688e8d04a451eac1d0fa69e6", "9a2c90c200c269688fee085301bf098b0a067c9e", "f2-c.renderer.2", "f2-c.domain.2", "load-production-baseline"),
    "f2d": ("152cb239cfcf3b6f76748429ee0dc6dcf97ebd1a", "9d91f1be70d5c36cba83e0395739a486f897dcdd", "f2-c.renderer.2", "f2-c.domain.2", "load-f2d"),
    "sr": ("86391a85971b59b460784cbea194cc5ec7e705bf", "25c424367381bb8439ab9cb7637cb13d6f8b980b", "f2-c.renderer.3", "f2-c.domain.5", "load-f2esr"),
}


def _fail(message: str) -> None:
    raise ValueError(message)


def _read(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {path.name}") from exc
    if not isinstance(value, dict): _fail(f"JSON root must be object: {path.name}")
    return value, "sha256:" + hashlib.sha256(raw).hexdigest()


def _required(mapping: dict, key: str, typ: type):
    value = mapping.get(key)
    if not isinstance(value, typ): _fail(f"missing/invalid {key}")
    return value


def _finite(value: object, *, positive: bool, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (value <= 0 if positive else value < 0):
        _fail(f"invalid numeric {label}")
    return float(value)


def _expected_files(directory: Path, labels: tuple[str, ...]) -> dict[tuple[str, int, int], Path]:
    expected = {(label, size, rep): directory / f"{label}-{size}-r{rep}.json"
                for label in labels for size in SIZES for rep in REPS}
    missing = [path.name for path in expected.values() if not path.is_file()]
    if missing: _fail(f"exact matrix incomplete; missing={missing}")
    return expected


def _check_http(row: dict, label: str, size: int, rep: int) -> None:
    jax, platform, renderer, domain, worktree = EXPECTED[label]
    environment = _required(row, "entorno", dict)
    if (environment.get("sha_harness") != HARNESS_SHA or environment.get("label") != label
            or environment.get("repetition") != rep or environment.get("respuesta_chars") != size
            or environment.get("sha_jax_esperado") != jax or environment.get("sha_jax_repo_path") != jax
            or environment.get("sha_plataforma_esperado") != platform or environment.get("max_turns") != 20):
        _fail("HTTP run metadata does not match hard-closed matrix")
    core = _required(environment, "core", dict)
    if (core.get("renderer"), core.get("domain"), core.get("lifecycle"), core.get("core_arity")) != (renderer, domain, "f2-d.lifecycle.2", 8):
        _fail("HTTP core versions invalid")
    origins = _required(core, "origins", dict)
    required_paths = {
        "governed_chat": f"/home/fruiz/worktrees/{worktree}-platform/backend/api/governed_chat.py",
        "governed_renderer": f"/home/fruiz/worktrees/{worktree}-jax/policy/governance/governed_renderer.py",
        "governed_domain": f"/home/fruiz/worktrees/{worktree}-jax/policy/governance/governed_domain.py",
        "output_lifecycle": f"/home/fruiz/worktrees/{worktree}-jax/policy/governance/output_lifecycle.py",
    }
    if origins != required_paths: _fail("HTTP module origins invalid")
    warmup = _required(row, "calentamiento", dict)
    if (warmup.get("c"), warmup.get("n"), warmup.get("ok"), warmup.get("errores"), warmup.get("degradados")) != (50, 1000, 1000, 0, 0):
        _fail("HTTP warmup invalid")
    levels = _required(row, "niveles", list)
    if len(levels) != len(LEVELS): _fail("HTTP levels invalid")
    for level, (concurrency, count) in zip(levels, LEVELS, strict=True):
        if not isinstance(level, dict) or (level.get("c"), level.get("n"), level.get("ok"), level.get("errores"), level.get("degradados")) != (concurrency, count, count, 0, 0):
            _fail("HTTP measurement level invalid")
        for key in ("rps", "p95_ms", "cpu_ms_por_peticion"):
            _finite(level.get(key), positive=True, label=f"HTTP {key}")
    if _required(row, "proveedor_falso_stats", dict).get("chat") != 2426: _fail("fake chat count invalid")
    final = _required(row, "bandeja_al_final", dict)
    if final.get("por_estado") != [{"state": "OUTPUT_COMMITTED_TO_TRANSPORT", "contract_state": "VALID", "filas": 2426}] or final.get("filas_por_n_eventos") != {"3": 2426}:
        _fail("final outbox evidence invalid")
    criterion = _required(row, "criterio", dict)
    if criterion.get("resultado") != "ACCEPTED": _fail("run criterion not ACCEPTED")
    _finite(_required(row, "log_backend", dict).get("deadlocks_1213"), positive=False, label="deadlocks_1213")


def _check_micro(row: dict, label: str, size: int, rep: int) -> None:
    jax, platform, renderer, domain, worktree = EXPECTED[label]
    if (row.get("mode"), row.get("repetition"), row.get("chars"), row.get("iterations"), row.get("warmup"),
            row.get("renderer_api_version"), row.get("domain_spec_version")) != (MICRO_LABELS[label], rep, size, 200, 20, renderer, domain):
        _fail("micro metadata/version invalid")
    if row.get("jax", {}).get("sha") != jax or row.get("platform", {}).get("sha") != platform:
        _fail("micro pair SHA invalid")
    roots = _required(row, "module_roots", dict)
    required = {"platform_governed_chat": f"/home/fruiz/worktrees/{worktree}-platform/backend/api/governed_chat.py",
                "jax_domain": f"/home/fruiz/worktrees/{worktree}-jax/policy/governance/governed_domain.py",
                "jax_renderer": f"/home/fruiz/worktrees/{worktree}-jax/policy/governance/governed_renderer.py",
                "jax_lifecycle": f"/home/fruiz/worktrees/{worktree}-jax/policy/governance/output_lifecycle.py"}
    if {key: roots.get(key) for key in required} != required or roots.get("envelope_schema_versions") != ["f2-c.1"] or roots.get("lifecycle_api_version") != "f2-d.lifecycle.2":
        _fail("micro origins/lifecycle invalid")
    samples = _required(row, "samples", list)
    if len(samples) != 200 or any(not isinstance(item, dict) for item in samples):
        _fail("micro samples invalid")
    wall = [_finite(item.get("wall_ms"), positive=False, label="micro wall_ms") for item in samples]
    cpu = [_finite(item.get("cpu_ms"), positive=False, label="micro cpu_ms") for item in samples]
    for key, values in (("median_wall_ms", wall), ("median_cpu_ms", cpu)):
        stored = _finite(row.get(key), positive=False, label=key)
        if not math.isclose(stored, statistics.median(values), rel_tol=0.0, abs_tol=1e-8):
            _fail("stored micro median differs from raw samples")


def _stats(values: list[float]) -> dict[str, float]:
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


def _first_measured_threshold(rows: list[dict]) -> int | None:
    for concurrency, _count in LEVELS:
        p95s = [next(item for item in row["niveles"] if item["c"] == concurrency)["p95_ms"] for row in rows]
        if statistics.median(p95s) > 500:
            return concurrency
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http-dir", type=Path, required=True)
    parser.add_argument("--micro-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        _fail("summary output must be outside the repository")
    if args.output.exists(): _fail("summary output must be a new artifact")
    http_paths = _expected_files(args.http_dir, HTTP_LABELS)
    micro_paths = _expected_files(args.micro_dir, tuple(MICRO_LABELS.values()))
    http: dict[tuple[str, int, int], dict] = {}
    micro: dict[tuple[str, int, int], dict] = {}
    provenance = {"http": {}, "micro": {}, "harness_sha": HARNESS_SHA}
    for key, path in http_paths.items():
        row, digest = _read(path); _check_http(row, *key); http[key] = row; provenance["http"][path.name] = digest
    for label in HTTP_LABELS:
        for size in SIZES:
            for rep in REPS:
                path = micro_paths[(MICRO_LABELS[label], size, rep)]
                row, digest = _read(path); _check_micro(row, label, size, rep); micro[(label, size, rep)] = row; provenance["micro"][path.name] = digest
    cells, micro_summary, deadlocks = {}, {}, {}
    for label in HTTP_LABELS:
        dead = []
        for size in SIZES:
            rows = [http[(label, size, rep)] for rep in REPS]
            cells[f"{label}:{size}"] = {"levels": {str(c): {
                "rps": _stats([next(item for item in row["niveles"] if item["c"] == c)["rps"] for row in rows]),
                "p95_ms": _stats([next(item for item in row["niveles"] if item["c"] == c)["p95_ms"] for row in rows]),
                "cpu_ms_per_request": _stats([next(item for item in row["niveles"] if item["c"] == c)["cpu_ms_por_peticion"] for row in rows]),
            } for c, _n in LEVELS}, "first_measured_p95_over_500": _first_measured_threshold(rows)}
            micro_rows = [micro[(label, size, rep)] for rep in REPS]
            micro_summary[f"{label}:{size}"] = {"wall_ms_median_of_medians": statistics.median(row["median_wall_ms"] for row in micro_rows),
                "cpu_ms_median_of_medians": statistics.median(row["median_cpu_ms"] for row in micro_rows)}
            dead.extend(row["log_backend"]["deadlocks_1213"] for row in rows)
        deadlocks[label] = {"sum": sum(dead), "min": min(dead), "max": max(dead)}
    plateaus = {key: {"peak_concurrency": max(value["levels"], key=lambda c: value["levels"][c]["rps"]["median"]),
                       "peak_rps": max(item["rps"]["median"] for item in value["levels"].values())}
                for key, value in cells.items()}
    deltas = {}
    for baseline in ("production-baseline", "f2d"):
        for size in SIZES:
            for c, _n in LEVELS:
                sr, other = cells[f"sr:{size}"]["levels"][str(c)], cells[f"{baseline}:{size}"]["levels"][str(c)]
                deltas[f"sr-vs-{baseline}:{size}:c{c}"] = {metric: (sr[metric]["median"] / other[metric]["median"] - 1) * 100
                    for metric in ("rps", "p95_ms", "cpu_ms_per_request")}
    output = {"matrix": {"labels": HTTP_LABELS, "sizes": SIZES, "repetitions": REPS, "levels": LEVELS},
              "cells": cells, "throughput_peak_observed": plateaus, "deltas_percent": deltas,
              "deadlocks_1213_background": deadlocks, "micro": micro_summary, "provenance": provenance}
    args.output.write_text(json.dumps(output, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__": main()
