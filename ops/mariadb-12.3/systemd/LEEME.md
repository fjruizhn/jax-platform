# Arranque de JAX después de su base

`jax-platform` y `jax-las-manos` arrancaban antes de que la MariaDB de JAX (Docker,
`mariadb-12-3-jax`) aceptara consultas y caían con `Lost connection to MySQL server
during query` (apagón del 2026-10-04). Esperar al puerto no basta: docker-proxy acepta en
`127.0.0.1:3308` antes de que MariaDB esté lista.

Ese día se curó solo en ~6 s gracias a `Restart=on-failure`, pero con 5 reintentos cada
5 s (`StartLimitBurst=5`) una base que tarde más de ~25 s —recuperación de InnoDB tras un
corte, un `MARIADB_AUTO_UPGRADE`, un reinicio de dockerd— deja a JAX abajo en silencio.

| Archivo | Va a |
|---|---|
| `jax-db-esperar` | `/usr/local/sbin/jax-db-esperar` (755 root) |
| `esperar-db.conf` | `/etc/systemd/system/jax-platform.service.d/` y `jax-las-manos.service.d/` |

- Corre como `ExecStartPre=+` en **cada** arranque del servicio (máquina, despliegue o
  reinicio), no solo en el de la máquina.
- Usa el `healthcheck.sh` de la imagen oficial (`--connect --innodb_initialized`), con el
  usuario de `.my-healthcheck.cnf` que ya existe en el datadir.
- Si la base no responde en 9 min, el arranque falla, avisa por `aviso-fallo@` (agrupa:
  uno cada 6 h por unidad) y `Restart=on-failure` lo vuelve a intentar.
- Al recibir el aviso: arreglar la base. JAX vuelve solo en el siguiente reintento (cada
  ~9 min); `sudo systemctl start jax-platform jax-las-manos` solo lo adelanta.
- El guion corre como root: fija su propio PATH/HOME/DOCKER_CONFIG y usa rutas absolutas,
  porque el entorno del servicio apunta a directorios de cuentas sin privilegios.
- El journal del servicio dice por qué esperó (primer y último error de `healthcheck.sh`).

`jax-ejecutor-proxy` y `jax-ariadna-pm` no se conectan a la base al arrancar (salen a los
13 s, antes que docker, sin errores): no llevan el drop-in.
