# Entrega de invitaciones de Virtual Office

El proceso web nunca entrega correo de invitación. `axioma-office-invitation-worker.timer`
activa una instancia `Type=oneshot` cada 30 segundos, y systemd no inicia otra
instancia del mismo servicio mientras la anterior sigue activa.

Antes de habilitar el timer, el operador ejecuta la migración de Office y crea
un snapshot completo del backend con venv propio. El worker nunca ejecuta el
checkout `jaxsvc`-escribible. Con el SHA aprobado exacto, ejecuta como root:

```bash
set -euo pipefail
SHA=<sha-aprobado-de-40-hex>
ORIGIN=https://github.com/fjruizhn/jax-platform.git
WHEELHOUSE=/opt/axioma-office-invitation-worker-wheelhouse
LOCK=$WHEELHOUSE/worker-requirements.lock
SOURCE=$(mktemp -d)
trap 'rm -rf "$SOURCE"' EXIT
git -C "$SOURCE" init -q
git -C "$SOURCE" remote add origin "$ORIGIN"
GIT_NO_REPLACE_OBJECTS=1 git -C "$SOURCE" fetch --no-tags origin "$SHA"
test -z "$(git -C "$SOURCE" replace -l)"
test ! -e "$SOURCE/.git/objects/info/alternates"
GIT_NO_REPLACE_OBJECTS=1 git -C "$SOURCE" fsck --strict --no-dangling
test "$(GIT_NO_REPLACE_OBJECTS=1 git -C "$SOURCE" rev-parse FETCH_HEAD^{commit})" = "$SHA"
GIT_NO_REPLACE_OBJECTS=1 git -C "$SOURCE" checkout --detach --force "$SHA"
read_runtime_value () {
  key=$1
  value=$(awk -F= -v key="$key" '$1 == key { if (++found != 1) exit 2; print substr($0, length(key) + 2) } END { exit found == 1 ? 0 : 1 }' /etc/jax/runtime.env)
  test -n "$value" && printf '%s\n' "$value"
}
JAX_DB_NAME=$(read_runtime_value JAX_DB_NAME)
JAX_DB_GRANTEE_HOST=$(read_runtime_value JAX_DB_GRANTEE_HOST)
case "$JAX_DB_NAME" in *[!A-Za-z0-9_]*|'') exit 2 ;; esac
case "$JAX_DB_GRANTEE_HOST" in ''|*%*|*_*|*[!A-Za-z0-9.:-]*) exit 2 ;; esac
install_from_audited_commit () {
  src=$1 dst=$2 mode=$3 tmp=$(mktemp -d)
  GIT_NO_REPLACE_OBJECTS=1 git -C "$SOURCE" archive "$SHA" "$src" | tar -x -C "$tmp"
  test -f "$tmp/$src" && test ! -L "$tmp/$src"
  sudo install -o root -g root -m "$mode" "$tmp/$src" "$dst"
  rm -rf "$tmp"
}
install_from_audited_commit ops/virtualoffice/invitation-worker-runtime-readiness.sh /usr/local/libexec/axioma-office-invitation-worker-readiness 0755
install_from_audited_commit ops/virtualoffice/install-invitation-worker-release.sh /usr/local/libexec/axioma-office-invitation-worker-install 0750
install_from_audited_commit ops/virtualoffice/provision-invitation-worker.sh /usr/local/libexec/axioma-office-invitation-worker-provision 0750
install_from_audited_commit ops/virtualoffice/install-invitation-delivery-routines.sh /usr/local/libexec/axioma-office-invitation-delivery-install 0750
install_from_audited_commit ops/virtualoffice/attest-invitation-delivery-routines.sh /usr/local/libexec/axioma-office-invitation-delivery-attest 0750
install_from_audited_commit ops/virtualoffice/office-invitation-delivery-v1.sql /usr/local/share/axioma-office-invitation-delivery-v1.sql 0644
install_from_audited_commit ops/virtualoffice/invitation-worker-artifact-readiness.sh /usr/local/libexec/axioma-office-invitation-worker-artifact-readiness 0750
sudo /usr/local/libexec/axioma-office-invitation-worker-provision --env /etc/jax/runtime.env
sudo /usr/local/libexec/axioma-office-invitation-delivery-install --db "$JAX_DB_NAME" --admin-transport /usr/local/libexec/axioma-mariadb-admin --worker-host "$JAX_DB_GRANTEE_HOST" --sql-file /usr/local/share/axioma-office-invitation-delivery-v1.sql --attestor /usr/local/libexec/axioma-office-invitation-delivery-attest
sudo /usr/local/libexec/axioma-office-invitation-worker-provision --env /etc/jax/runtime.env
test ! -L "$WHEELHOUSE"
if [[ ! -e "$WHEELHOUSE" ]]; then
  SUPPLY_STAGE=$(mktemp -d /opt/.axioma-office-invitation-wheelhouse.XXXXXX)
  GIT_NO_REPLACE_OBJECTS=1 git -C "$SOURCE" archive "$SHA" ops/virtualoffice/worker-requirements.lock | tar -x -C "$SUPPLY_STAGE"
  mv "$SUPPLY_STAGE/ops/virtualoffice/worker-requirements.lock" "$SUPPLY_STAGE/worker-requirements.lock"
  rm -r "$SUPPLY_STAGE/ops"
  chown jaxbuild:jaxbuild "$SUPPLY_STAGE" "$SUPPLY_STAGE/worker-requirements.lock"
  chmod 0755 "$SUPPLY_STAGE"
  runuser -u jaxbuild -- python3.14 -m pip download --no-cache-dir --only-binary=:all: --require-hashes --no-deps --dest "$SUPPLY_STAGE" -r "$SUPPLY_STAGE/worker-requirements.lock"
  chown -R root:root "$SUPPLY_STAGE"
  mv "$SUPPLY_STAGE" "$WHEELHOUSE"
fi
test -f "$LOCK" && test ! -L "$LOCK"
sudo /usr/local/libexec/axioma-office-invitation-worker-install --origin "$ORIGIN" --revision "$SHA" --wheelhouse "$WHEELHOUSE" --lock "$LOCK"
install_from_audited_commit ops/virtualoffice/invitation-worker.service /etc/systemd/system/axioma-office-invitation-worker.service 0644
install_from_audited_commit ops/virtualoffice/invitation-worker.timer /etc/systemd/system/axioma-office-invitation-worker.timer 0644
systemctl daemon-reload
/usr/local/libexec/axioma-office-invitation-worker-artifact-readiness "$SOURCE"
systemd-run --wait --pipe --property=User=jaxinvite --property=EnvironmentFile=/etc/jax/invitation-worker.env /usr/local/libexec/axioma-office-invitation-worker-readiness
systemd-run --wait --pipe --working-directory=/opt/axioma-office-invitation-worker/backend --property=User=jaxinvite --property=EnvironmentFile=/etc/jax/invitation-worker.env /opt/axioma-office-invitation-worker/backend/.venv/bin/python -Es -m virtual_office.invitation_smtp_preflight
systemctl enable --now axioma-office-invitation-worker.timer
/usr/local/libexec/axioma-office-invitation-worker-artifact-readiness "$SOURCE" --effective
```

El wheelhouse se construye como `jaxbuild` a partir del lock del SHA auditado,
con Python 3.14 y solo ruedas cuyos hashes aparecen en el lock. Después queda
`root:root`; el instalador revisa recursivamente tipos, enlaces, propietario y
permisos, y ejecuta `pip --no-index --require-hashes --no-deps` como `jaxbuild`.
Si ya existe el directorio de suministro, se conserva y el instalador exige que
su lock coincida byte a byte con el del release. Una actualización del lock
requiere preparar otro wheelhouse auditado antes de instalar.

El provisionador crea el usuario de sistema y principal MariaDB `jaxinvite`. El
principal recibe solamente SELECT para configuración y las columnas de observación
del outbox, más EXECUTE en los tres procedimientos auditados. No recibe privilegios
directos para emitir, revocar, expirar o liquidar invitaciones, administrar usuarios,
ni DDL. El instalador sólo acepta el SQL con el SHA-256 fijado y siempre ejecuta el
atestado instalado root-owned antes de devolver éxito.

`/etc/jax/invitation-worker.env` es el único `EnvironmentFile` del worker. Es
`root:root 0600` e incluye sólo conexión del principal, `FERNET_KEY` y las
variables Office necesarias para descifrar, validar y entregar una invitación. El
worker falla antes de reclamar una fila si falta configuración de invitación o SMTP.

Salud y recuperación:

```bash
systemd-run --wait --pipe --property=User=jaxinvite --property=EnvironmentFile=/etc/jax/invitation-worker.env /usr/local/libexec/axioma-office-invitation-worker-readiness
systemd-run --wait --pipe --working-directory=/opt/axioma-office-invitation-worker/backend --property=User=jaxinvite --property=EnvironmentFile=/etc/jax/invitation-worker.env /opt/axioma-office-invitation-worker/backend/.venv/bin/python -Es -m virtual_office.invitation_worker_health
systemctl status axioma-office-invitation-worker.timer axioma-office-invitation-worker.service
```

La salud informa solamente conteos `queued`, `claimed` y `stale`. Un `stale > 0`
sale con estado 2: el siguiente worker convierte el intento abandonado en `UNKNOWN`;
nunca lo devuelve a `QUEUED`. `OnFailure=aviso-fallo@%n.service` usa el template
de alerta auditado del host; el readiness efectivo verifica que existe antes de producción.
