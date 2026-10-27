#!/usr/bin/env bash
# Root-only SQL transport for Office's separate MariaDB container.
set -euo pipefail
[[ $# -eq 0 ]] || { printf '%s\n' 'SQL is accepted only on stdin' >&2; exit 2; }
exec docker exec -i axioma-office-mariadb sh -ec \
  'MYSQL_PWD="$(cat /run/secrets/office_mariadb_root_password)" exec mariadb -uroot --batch --skip-column-names'
