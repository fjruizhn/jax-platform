"""Read-only F2-E-SR/F2-D revalidation microbenchmark.

Each invocation measures one exact clean JAX/Platform pair and one explicit
text size.  It imports no production path, opens no port or database, and
writes only the requested raw-sample JSON artifact.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
from pathlib import Path
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone


ITERATIONS = 200
WARMUP = 20
SIZES = (8_000, 16_000)
VERSION_EXPECTATIONS = {
    "production-baseline": ("f2-c.renderer.2", "f2-c.domain.2"),
    "f2d": ("f2-c.renderer.2", "f2-c.domain.2"),
    "f2esr": ("f2-c.renderer.3", "f2-c.domain.5"),
}
ORIGIN_SUFFIXES = {"jax": "fjruizhn/Jax.git", "platform": "fjruizhn/jax-platform.git"}


def _git(directory: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(directory), *args], text=True).strip()


def verify_checkout(directory: Path, exact_sha: str, role: str) -> dict[str, str]:
    if not directory.is_dir() or not (directory / ".git").exists():
        raise ValueError("explicit repository directory is not a checkout")
    if len(exact_sha) != 40 or any(char not in "0123456789abcdef" for char in exact_sha):
        raise ValueError("exact SHA must be a lowercase 40-hex OID")
    head = _git(directory, "rev-parse", "HEAD")
    if head != exact_sha:
        raise ValueError("checkout HEAD differs from requested exact SHA")
    if _git(directory, "status", "--porcelain=v1"):
        raise ValueError("benchmark checkout must be clean")
    origin = _git(directory, "remote", "get-url", "origin")
    if not origin.endswith(ORIGIN_SUFFIXES[role]):
        raise ValueError("benchmark checkout has an unexpected origin")
    return {"sha": head, "origin": origin}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=tuple(VERSION_EXPECTATIONS))
    parser.add_argument("--jax-dir", required=True, type=Path)
    parser.add_argument("--platform-dir", required=True, type=Path)
    parser.add_argument("--jax-sha", required=True)
    parser.add_argument("--platform-sha", required=True)
    parser.add_argument("--chars", required=True, type=int, choices=SIZES)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repetition", required=True, type=int)
    parser.add_argument("--worker", action="store_true")
    return parser.parse_args()


def _inside(module: object, root: Path) -> str:
    source = Path(getattr(module, "__file__", "")).resolve()
    if not source.is_relative_to(root.resolve()):
        raise ValueError("loaded module is outside its exact checkout")
    return str(source)


class _Contract:
    """The exact non-claim provider shape consumed by the Platform bridge."""
    contract_parsed = True
    claims: list[object] = []
    judgment = None
    def __init__(self, analysis: str) -> None:
        self.analysis = analysis


def build_unit(jax_dir: Path, platform_dir: Path, mode: str, chars: int):
    # This worker has PYTHONPATH=<exact platform/backend>:<exact JAX>, so the
    # production bridge itself selects and proves the paired core.
    from api import governed_chat  # pylint: disable=import-outside-toplevel
    from jax.memory.b9 import ScopeContext  # pylint: disable=import-outside-toplevel
    import policy.governance.governed_domain as domain  # pylint: disable=import-outside-toplevel
    import policy.governance.governed_renderer as renderer  # pylint: disable=import-outside-toplevel
    import policy.governance.output_lifecycle as lifecycle_module  # pylint: disable=import-outside-toplevel
    platform_source = _inside(governed_chat, platform_dir / "backend")
    domain_source = _inside(domain, jax_dir)
    renderer_source = _inside(renderer, jax_dir)
    lifecycle_source = _inside(lifecycle_module, jax_dir)
    expected = VERSION_EXPECTATIONS[mode]
    if (domain.GOVERNED_RENDERER_API_VERSION, domain.GOVERNED_DOMAIN_SPEC_VERSION) != expected:
        raise ValueError("checkout governance versions do not match benchmark mode")
    if domain.GOVERNED_ENVELOPE_SCHEMA_VERSIONS != frozenset({"f2-c.1"}):
        raise ValueError("unexpected governed envelope schema set")
    lifecycle = governed_chat._lifecycle_core()
    if _inside(lifecycle, jax_dir) is None or lifecycle.OUTPUT_LIFECYCLE_API_VERSION != "f2-d.lifecycle.2":
        raise ValueError("unexpected lifecycle core")
    base = "Respuesta de carga sobre planificacion y presupuesto del trimestre. "
    text = (base * (chars // len(base) + 1))[:chars]
    scope = ScopeContext(actor_principal="user:1", actor_type="USER", subject_user_id="1",
                         tenant_id="1", project_id=None, calling_component="jax-platform-web-chat")
    started = time.perf_counter_ns()
    projection = governed_chat.project_provider_contract(_Contract(text), memory_scope=scope, user_id="1",
        request_id="f2esr-micro-request", trace_id="f2esr-micro-trace")
    project_ms = (time.perf_counter_ns() - started) / 1_000_000
    if projection.transport_unit is None or projection.contract_degraded or projection.text != text:
        raise ValueError("real Platform provider bridge did not mint a valid transport unit")
    return projection.transport_unit, lifecycle.revalidate_for_transport, domain.GOVERNED_RENDERER_API_VERSION, \
        domain.GOVERNED_DOMAIN_SPEC_VERSION, project_ms, {
            "platform_governed_chat": platform_source, "jax_domain": domain_source,
            "jax_renderer": renderer_source, "jax_lifecycle": lifecycle_source,
            "envelope_schema_versions": sorted(domain.GOVERNED_ENVELOPE_SCHEMA_VERSIONS),
            "lifecycle_api_version": lifecycle.OUTPUT_LIFECYCLE_API_VERSION,
        }


def _worker_environment(args: argparse.Namespace) -> dict[str, str]:
    allowed = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "TZ", "HOME"}}
    allowed.update({
        "PYTHONPATH": str((args.platform_dir / "backend").resolve()) + os.pathsep + str(args.jax_dir.resolve()),
        "JAX_REPO_PATH": str(args.jax_dir.resolve()), "JAX_ENVIRONMENT": "test",
        "JAX_JWT_SECRET": secrets.token_urlsafe(48),
        "FERNET_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "JAX_DB_HOST": "127.0.0.1", "JAX_DB_PORT": "3308", "JAX_DB_USER": "benchmark",
        "JAX_DB_PASSWORD": "not-a-real-password", "JAX_DB_NAME": "f2esr_micro_no_db",
    })
    return allowed


def percentile_95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[(len(ordered) * 95 + 99) // 100 - 1]


def main() -> None:
    args = parse_args()
    if args.repetition < 1:
        raise SystemExit("repetition must be positive")
    jax = verify_checkout(args.jax_dir.resolve(), args.jax_sha, "jax")
    platform = verify_checkout(args.platform_dir.resolve(), args.platform_sha, "platform")
    if not args.worker:
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--mode", args.mode,
                   "--jax-dir", str(args.jax_dir.resolve()), "--platform-dir", str(args.platform_dir.resolve()),
                   "--jax-sha", args.jax_sha, "--platform-sha", args.platform_sha,
                   "--chars", str(args.chars), "--repetition", str(args.repetition), "--output", str(args.output.resolve())]
        subprocess.run(command, check=True, env=_worker_environment(args))
        return
    unit, revalidate, renderer_version, domain_version, project_ms, module_roots = build_unit(
        args.jax_dir.resolve(), args.platform_dir.resolve(), args.mode, args.chars)
    now = datetime.now(timezone.utc)
    for _ in range(WARMUP):
        revalidate(unit, now)
    samples = []
    for _ in range(ITERATIONS):
        wall_start, cpu_start = time.perf_counter_ns(), time.process_time_ns()
        revalidate(unit, now)
        samples.append({"wall_ms": (time.perf_counter_ns() - wall_start) / 1_000_000,
                        "cpu_ms": (time.process_time_ns() - cpu_start) / 1_000_000})
    wall = [sample["wall_ms"] for sample in samples]
    cpu = [sample["cpu_ms"] for sample in samples]
    result = {"mode": args.mode, "repetition": args.repetition, "chars": args.chars,
              "iterations": ITERATIONS, "warmup": WARMUP, "jax": jax, "platform": platform,
              "renderer_api_version": renderer_version, "domain_spec_version": domain_version,
              "project_provider_contract_ms": project_ms, "module_roots": module_roots,
              "median_wall_ms": statistics.median(wall), "p95_wall_ms": percentile_95(wall),
              "median_cpu_ms": statistics.median(cpu), "p95_cpu_ms": percentile_95(cpu),
              "samples": samples}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
