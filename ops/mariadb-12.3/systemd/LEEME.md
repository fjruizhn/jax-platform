# Arranque de JAX después de su base

`jax-platform` y `jax-las-manos` arrancaban antes de que la MariaDB de JAX (Docker,
`mariadb-12-3-jax`) aceptara consultas y caían con `Lost connection to MySQL server
during query` (apagón del 2026-10-04). Esperar al puerto no basta: docker-proxy acepta en
`127.0.0.1:3308` antes de que MariaDB esté lista.

| Archivo | Va a |
|---|---|
| `jax-db-esperar` | `/usr/local/sbin/jax-db-esperar` (755 root) |
| `jax-db-lista.service` | `/etc/systemd/system/` + `systemctl enable` |
| `esperar-db.conf` | `/etc/systemd/system/jax-platform.service.d/` y `jax-las-manos.service.d/` |

Usa el `healthcheck.sh` de la imagen oficial (`--connect --innodb_initialized`), con el
usuario de `.my-healthcheck.cnf` que ya existe en el datadir. Si la base no responde en
9 min, la unidad falla, avisa por `aviso-fallo@` y JAX no arranca (fallo cerrado).

`jax-ejecutor-proxy` y `jax-ariadna-pm` no se conectan a la base al arrancar: no llevan
el drop-in.
