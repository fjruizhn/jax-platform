# Entrega de invitaciones de Virtual Office

El proceso web nunca entrega correo de invitación. `axioma-office-invitation-worker.timer`
activa una instancia `Type=oneshot` cada 30 segundos, y systemd no inicia otra
instancia del mismo servicio mientras la anterior sigue activa.

Antes de habilitar el timer, el operador ejecuta la migración de Office y crea
un snapshot completo del backend con venv propio. El worker nunca ejecuta el
checkout `jaxsvc`-escribible. Con el SHA aprobado exacto, ejecuta como root:

```bash
set -euo pipefail
SHA='SHA_COMPLETO_AUDITADO'
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
case "$JAX_DB_NAME" in *[!A-Za-z0-9_]*|'') exit 2 ;; esac
PROBE_DIR=$(sudo mktemp -d /run/axioma-office-db-probe.XXXXXXXX)
trap 'sudo rm -rf -- "$PROBE_DIR"; rm -rf -- "$SOURCE"' EXIT
sudo python3 - "$PROBE_DIR/client.cnf" <<'PY'
from pathlib import Path
import os, sys
values = {}
for line in Path('/etc/jax/runtime.env').read_text().splitlines():
    if not line or line.startswith('#'):
        continue
    key, value = line.split('=', 1)
    if key in values:
        raise SystemExit('duplicate runtime key')
    values[key] = value
needed = ('JAX_DB_HOST', 'JAX_DB_PORT', 'JAX_DB_USER', 'JAX_DB_PASSWORD')
if any(not values.get(key) or '\n' in values[key] or '\r' in values[key] for key in needed):
    raise SystemExit('incomplete runtime DB credentials')
path = Path(sys.argv[1])
with path.open('x') as file:
    os.fchmod(file.fileno(), 0o600)
    file.write('[client]\n')
    for option, key in (('host', 'JAX_DB_HOST'), ('port', 'JAX_DB_PORT'), ('user', 'JAX_DB_USER'), ('password', 'JAX_DB_PASSWORD')):
        file.write(f'{option}={values[key]}\n')
PY
MEASURED_GRANTEE_HOST=$(sudo mariadb --defaults-extra-file="$PROBE_DIR/client.cnf" --batch --skip-column-names "$JAX_DB_NAME" -e "SELECT SUBSTRING_INDEX(CURRENT_USER(),'@',-1)")
case "$MEASURED_GRANTEE_HOST" in ''|*%*|*_*|*[!A-Za-z0-9.:-]*) exit 2 ;; esac
sudo rm -rf -- "$PROBE_DIR"
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
ATTESTOR_SHA256=$(GIT_NO_REPLACE_OBJECTS=1 git -C "$SOURCE" show "$SHA:ops/virtualoffice/attest-invitation-delivery-routines.sh" | sha256sum | awk '{print $1}')
test "$(sudo sha256sum /usr/local/libexec/axioma-office-invitation-delivery-attest | awk '{print $1}')" = "$ATTESTOR_SHA256"
install_from_audited_commit ops/virtualoffice/office-invitation-delivery-v1.sql /usr/local/share/axioma-office-invitation-delivery-v1.sql 0644
install_from_audited_commit ops/virtualoffice/invitation-worker-artifact-readiness.sh /usr/local/libexec/axioma-office-invitation-worker-artifact-readiness 0750
install_from_audited_commit ops/virtualoffice/enable-invitation-worker-timer.sh /usr/local/libexec/axioma-office-invitation-worker-enable 0750
sudo /usr/local/libexec/axioma-office-invitation-worker-provision --env /etc/jax/runtime.env --measured-grantee-host "$MEASURED_GRANTEE_HOST"
sudo /usr/local/libexec/axioma-office-invitation-delivery-install --db "$JAX_DB_NAME" --admin-transport /usr/local/libexec/axioma-mariadb-admin --worker-host "$MEASURED_GRANTEE_HOST" --sql-file /usr/local/share/axioma-office-invitation-delivery-v1.sql --attestor /usr/local/libexec/axioma-office-invitation-delivery-attest
sudo /usr/local/libexec/axioma-office-invitation-worker-provision --env /etc/jax/runtime.env --measured-grantee-host "$MEASURED_GRANTEE_HOST" --attestor /usr/local/libexec/axioma-office-invitation-delivery-attest --attestor-sha256 "$ATTESTOR_SHA256"
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
sudo /usr/local/libexec/axioma-office-invitation-worker-enable --source "$SOURCE"
```

El wheelhouse se construye como `jaxbuild` a partir del lock del SHA auditado,
con Python 3.14 y solo ruedas cuyos hashes aparecen en el lock. Después queda
`root:root`; el instalador revisa recursivamente tipos, enlaces, propietario y
permisos, y ejecuta `pip --no-index --require-hashes --no-deps` como `jaxbuild`.
Si ya existe el directorio de suministro, se conserva y el instalador exige que
su lock coincida byte a byte con el del release. Una actualización del lock
requiere preparar otro wheelhouse auditado antes de instalar.

El provisionador recibe el host grantee medido explícitamente; no lo toma de
`runtime.env`. Con ese host crea el usuario de sistema y principal MariaDB `jaxinvite`. El
principal recibe solamente SELECT para configuración y las columnas de observación
del outbox, más EXECUTE en los tres procedimientos auditados. No recibe privilegios
directos para emitir, revocar, expirar o liquidar invitaciones, administrar usuarios,
ni DDL. El instalador sólo acepta el SQL con el SHA-256 fijado y siempre ejecuta el
atestado instalado root-owned antes de devolver éxito. El segundo llamado al
provisionador no escribe MariaDB: compara los grants completos, fija el hash del
atestado contra el SHA auditado y vuelve a atestar la capacidad terminada.

`/etc/jax/invitation-worker.env` es el único `EnvironmentFile` del worker. Es
`root:root 0600` e incluye sólo conexión del principal, `FERNET_KEY`,
`JAX_ENVIRONMENT`, `OFFICE_PUBLIC_ORIGIN` y las variables Office necesarias para
descifrar, validar y entregar una invitación. En producción el origen de cada
enlace debe coincidir exactamente con el origen Office canónico; el worker falla
antes de reclamar una fila si falta esa configuración, no es canónica, o SMTP no
está listo.

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
