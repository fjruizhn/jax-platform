#!/usr/bin/env bash
# Create the two separate service identities used by the Office units.
set -euo pipefail
[[ $(id -u) == 0 ]] || { printf '%s\n' 'root required' >&2; exit 1; }
for account in officeweb officemigrate; do
  if ! getent passwd "$account" >/dev/null; then
    useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$account"
  fi
  entry=$(getent passwd "$account")
  IFS=: read -r name _ uid gid _ home shell <<< "$entry"
  [[ $name == "$account" && $home == /nonexistent && $shell == /usr/sbin/nologin ]] || {
    printf '%s\n' "invalid Office service identity: $account" >&2; exit 1;
  }
  [[ $uid != 0 && $gid == "$(getent group "$account" | cut -d: -f3)" && $(id -nG "$account") == "$account" ]] || {
    printf '%s\n' "Office service identity has shared or supplemental groups: $account" >&2; exit 1;
  }
done
