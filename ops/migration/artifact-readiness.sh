#!/usr/bin/env bash
# Sourceable validation for the immutable Platform artifact used by systemd.

verify_platform_artifact_readiness() {
  local artifact_root backend executable unit effective
  artifact_root=$1
  shift
  [ "$#" -gt 0 ] || set -- systemctl
  backend="$artifact_root/backend"
  [ -d "$backend" ] || { printf 'artifact readiness: missing backend directory: %s\n' "$backend" >&2; return 1; }
  for unit in jax-platform.service jax-platform-migrate.service; do
    if [ "$unit" = jax-platform.service ]; then executable="$backend/.venv/bin/uvicorn"; else executable="$backend/.venv/bin/python"; fi
    [ -x "$executable" ] || { printf 'artifact readiness: missing executable for %s: %s\n' "$unit" "$executable" >&2; return 1; }
    effective="$("$@" show "$unit" -p WorkingDirectory -p ExecStart --no-pager)" || {
      printf 'artifact readiness: cannot inspect %s\n' "$unit" >&2; return 1;
    }
    printf '%s\n' "$effective" | grep -Fqx "WorkingDirectory=$backend" || {
      printf 'artifact readiness: %s WorkingDirectory differs from artifact\n' "$unit" >&2; return 1;
    }
    printf '%s\n' "$effective" | grep -Fq "ExecStart={ path=$executable ;" || {
      printf 'artifact readiness: %s ExecStart differs from artifact\n' "$unit" >&2; return 1;
    }
  done
}
