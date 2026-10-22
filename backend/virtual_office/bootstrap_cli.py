"""Local explicit bootstrap command; access tokens are accepted only on stdin."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from auth.jwt import decode_token
from auth.middleware import verificar_sesion

from .bootstrap import BootstrapDenied, bootstrap_initial_office_root


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bootstrap-office-root")
    parser.add_argument("--target-user-id", type=int, required=True)
    parser.add_argument("--expected-email", required=True)
    parser.add_argument("--basis-ref", required=True)
    parser.add_argument("--access-token-stdin", action="store_true", required=True)
    return parser


async def _run(args: argparse.Namespace) -> int:
    token = sys.stdin.readline().strip()
    if not token:
        return 2
    try:
        user = await verificar_sesion(decode_token(token), "access")
        receipt = await bootstrap_initial_office_root(
            session_user=user, target_user_id=args.target_user_id,
            expected_email=args.expected_email, basis_ref=args.basis_ref,
        )
    except (BootstrapDenied, ValueError):
        return 1
    print(json.dumps({"ceremony_key": receipt.ceremony_key, "target_user_id": receipt.target_user_id,
                      "replay": receipt.replay}, separators=(",", ":")))
    return 0


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
