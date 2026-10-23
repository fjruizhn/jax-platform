#!/usr/bin/env python3
"""Isolated launcher that establishes only the trusted sibling backend path."""
from __future__ import annotations

import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[2] / "backend"


def main(argv: list[str]) -> int:
    if not BACKEND.is_dir():
        return 2
    # `-Es` ignores Python environment overrides and user site packages. The
    # release-owned sibling is the one explicit application import root.
    sys.path.insert(0, str(BACKEND))
    import virtual_office

    if argv == ["--check-import"]:
        return 0
    if argv == ["--smtp-preflight"]:
        from virtual_office.invitation_smtp_preflight import main as preflight_main

        return preflight_main()
    if argv == ["--health"]:
        from virtual_office.invitation_worker_health import main as health_main

        return health_main()
    if argv:
        return 2
    from virtual_office.invitation_delivery_worker import main as worker_main
    return worker_main()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
