#!/usr/bin/env bash
# Root-controlled release selector; validate the exact JAX/JXP pair before use.
set -euo pipefail
root=${1:-/opt/jax-platform-pair}
expected_jxp=${2:-}
expected_jax=${3:-}
[[ $root == /opt/jax-platform-pair ]] || { echo 'pair artifact readiness: unsafe root' >&2; exit 2; }
[[ -L "$root/current" ]] || { echo 'pair artifact readiness: current selector missing' >&2; exit 1; }
release=$(readlink -f -- "$root/current")
[[ $release == "$root/releases/"* && -d $release ]] || { echo 'pair artifact readiness: current selector escapes releases' >&2; exit 1; }
args=("$release")
[[ -n $expected_jxp ]] && args+=("$expected_jxp")
[[ -n $expected_jax ]] || [[ -z $expected_jxp ]] || { echo 'pair artifact readiness: incomplete expectations' >&2; exit 2; }
[[ -n $expected_jax ]] && args+=("$expected_jax")
exec python3 "$(dirname "${BASH_SOURCE[0]}")/pair_artifact_readiness.py" "${args[@]}"
