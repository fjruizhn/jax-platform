#!/usr/bin/env bash
# Fixed root-admin SQL transport for the production MariaDB container.
set -euo pipefail

[[ $# -eq 0 ]] || { printf '%s\n' 'this transport accepts SQL only on stdin' >&2; exit 2; }
exec docker exec -i mariadb-12-3-jax sh -ec 'MYSQL_PWD="$(cat /run/secrets/mariadb_root_password)" exec mariadb -uroot --batch --skip-column-names'
