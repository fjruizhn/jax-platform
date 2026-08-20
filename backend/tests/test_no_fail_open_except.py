#!/usr/bin/env python3
"""P10 — ningún validador o gate puede fallar abierto ante error o
ausencia de señal, incluyendo vía excepción sin capturar (REFORMAS-v3.1.md,
Apendice C-bis, jax/six-impossible-things.html).

Copia de jax/policy/tests/test_no_fail_open_except.py (ronda 4, 2026-08-20,
T3). NO es un import compartido: jax-platform y jax son repos GitHub
privados separados, y un checkout cruzado en CI requeriría un PAT/secret
nuevo -- decisión de infraestructura que esta sesión no tomó. Cada repo
vigila su propio árbol con su propia copia; la duplicación es el costo de
no introducir una credencial compartida sin autorización explícita. Si se
edita la lógica de detección en un lado, hay que replicarlo en el otro
(anotado como deuda de sincronización, no resuelta con un paquete
instalable compartido -- esa es la opción más limpia para una sesión
futura, ver CONTEXT.md de jax).

Enforcement mecánico y acotado, no un analizador general: un bloque
`except` cuyo cuerpo es únicamente `pass` (o `pass` + comentarios) traga
el error sin propagarlo, sin loguearlo y sin dejar ningún rastro.

Marcado, no allowlist: un except-pass legítimo (fail-soft real: nadie
depende de que esa operación haya funcionado) se marca con un comentario
en la MISMA línea del `except`, formato `# fail-soft: <razón específica>`.
Sin esa marca, es una violación.

Corre con:
  python3 backend/tests/test_no_fail_open_except.py
"""
from __future__ import annotations

import ast
import linecache
import sys
from pathlib import Path

_THIS_REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOTS = [_THIS_REPO_ROOT]

EXCLUDE_DIR_NAMES = {
    ".venv", "venv", "node_modules", ".git", ".worktrees", "worktrees",
    "__pycache__", "dist", "build",
}

FAIL_SOFT_MARKER = "# fail-soft:"


def _iter_python_files():
    for root in REPO_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if any(part in EXCLUDE_DIR_NAMES for part in path.parts):
                continue
            yield path


def _is_bare_pass_except(node: ast.ExceptHandler) -> bool:
    body = [
        stmt for stmt in node.body
        if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Constant)
        or not isinstance(stmt.value.value, str)  # descarta docstrings/comentarios-como-string
    ]
    return len(body) == 1 and isinstance(body[0], ast.Pass)


def find_fail_open_excepts() -> list[str]:
    linecache.clearcache()
    violations = []
    for path in _iter_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _is_bare_pass_except(node):
                source_line = linecache.getline(str(path), node.lineno)
                if FAIL_SOFT_MARKER in source_line:
                    continue
                violations.append(f"{path}:{node.lineno}")
    return violations


def test_no_fail_open_except() -> None:
    violations = find_fail_open_excepts()
    assert not violations, (
        f"{len(violations)} except-pass (fail-open) sin marcar '# fail-soft: <razón>':\n"
        + "\n".join(violations)
    )


def main() -> int:
    violations = find_fail_open_excepts()
    if violations:
        print(f"FAIL — {len(violations)} except-pass (fail-open) encontrados:")
        for v in violations:
            print(f"  {v}")
        return 1
    print("OK — cero except-pass silenciosos en el codigo fuente escaneado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
