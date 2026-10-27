#!/usr/bin/env bash
# Create the two separate service identities used by the Office units.
set -euo pipefail
[[ $(id -u) == 0 ]] || { printf '%s\n' 'root required' >&2; exit 1; }
for account in officeweb officemigrate; do
  if ! getent passwd "$account" >/dev/null; then
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$account"
  fi
  entry=$(getent passwd "$account")
  IFS=: read -r name _ uid gid _ home shell <<< "$entry"
  [[ $name == "$account" && $home == /nonexistent && $shell == /usr/sbin/nologin ]] || {
    printf '%s\n' "invalid Office service identity: $account" >&2; exit 1;
  }
done
