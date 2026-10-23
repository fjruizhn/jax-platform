#!/usr/bin/env bash
# Sourceable validation for the immutable Platform artifact used by systemd.

verify_platform_artifact_readiness() {
  local artifact_root helper
  artifact_root=$1
  shift
  [ "$#" -gt 0 ] || set -- busctl
  helper="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/artifact_readiness.py"
  [ -r "$helper" ] || { printf 'artifact readiness: verifier unavailable\n' >&2; return 1; }
  python3 "$helper" "$artifact_root" "$@"
}
