# Oficina virtual: MariaDB y API propios

El despliegue de `virtualoffice.axioma-ia.io` usa la MariaDB `axioma-office-mariadb`
en `127.0.0.1:3310`, los usuarios OS `officeweb`, `officemigrate` y
`officeinvite`, y los archivos root-owned bajo `/etc/axioma-office`. No se
aplican las migraciones ni los imports a `jax_memory`. El primer propietario
conserva su hash de contraseña; el cambio posterior se hace en «Seguridad de
la cuenta» dentro de Oficina.

## 1. Fijar el release

`SHA` es el commit con CI verde y veredicto Sol sobre ese mismo SHA. Descargarlo
de `origin` a un repositorio efímero root-owned, exigir `fsck` y coincidencia
exacta de `FETCH_HEAD`. Instalar desde ese objeto, nunca desde el worktree
modificable:

```bash
set -euo pipefail
SHA='SHA_COMPLETO_AUDITADO'
SOURCE=$(sudo mktemp -d /var/tmp/office-reviewed.XXXXXXXX)
sudo git -C "$SOURCE" init -q
sudo git -C "$SOURCE" remote add origin https://github.com/fjruizhn/jax-platform.git
sudo env GIT_NO_REPLACE_OBJECTS=1 git -C "$SOURCE" fetch --no-tags origin "$SHA"
test "$(sudo env GIT_NO_REPLACE_OBJECTS=1 git -C "$SOURCE" rev-parse FETCH_HEAD^{commit})" = "$SHA"
sudo env GIT_NO_REPLACE_OBJECTS=1 git -C "$SOURCE" fsck --strict --no-dangling
STAGE=$(sudo mktemp /var/tmp/office-base-install.XXXXXXXX)
sudo env GIT_NO_REPLACE_OBJECTS=1 git -C "$SOURCE" show "$SHA:ops/virtualoffice/install-office-base-runtime.sh" | sudo tee "$STAGE" >/dev/null
sudo install -o root -g root -m 0750 "$STAGE" /usr/local/libexec/axioma-office-base-install
sudo unlink "$STAGE"
```

## 2. Crear la base independiente

Medir los `server_id` de las MariaDB existentes y elegir uno diferente. El
instalador crea un secreto raíz propio, copia Compose, transporte y unidad
desde `SHA`, y atesta los bytes instalados. No inicia ni altera JAX.

```bash
sudo /usr/local/libexec/axioma-office-base-install \
  --source "$SOURCE" --revision "$SHA" --phase database --server-id ID_UNICO
sudo systemctl enable --now axioma-office-database.service
sudo /usr/local/libexec/axioma-office-base-artifact-readiness "$SOURCE" "$SHA" --phase database
sudo /usr/local/libexec/axioma-office-mariadb-admin <<<'SELECT @@server_id,@@hostname;'
```

El resultado debe ser `ID_UNICO` y `axioma-office-db`; Compose crea el esquema
`axioma_office` separado. Conservar `/var/lib/axioma-office-mariadb` en el respaldo; probar
restauración en otra instancia antes de cambiar el ingreso público.

## 3. Principales, migración y propietario

Preparar `/etc/axioma-office/deployment.env` como `root:root 0600` con el
esquema `axioma_office`, host `127.0.0.1`, puerto `3310`, `OFFICE_DEDICATED_DB=1`,
`OFFICE_DB_SERVER_ID=ID_UNICO`, `OFFICE_DB_HOSTNAME=axioma-office-db`,
`OFFICE_MFA_POLICY=optional`, origen público HTTPS, secretos nuevos de Oficina
y nombres de principales DB distintos de JAX. El provisionador existente
`ops/migration/provision-db-runtime.sh`, instalado desde el release aprobado,
recibe `--env /etc/axioma-office/deployment.env`,
`--state-root /etc/axioma-office`, `--admin-transport
/usr/local/libexec/axioma-office-mariadb-admin` y el grantee host **medido**
desde la conexión al contenedor. Pasar `--skip-os-identities`: los usuarios OS
los instala el helper de Oficina, y este provisionador compartido no debe
crear ni modificar `jaxweb`/`jaxmigrate`. Primero ejecutarlo con `--migrator-only`,
instalar el par de release del mismo `SHA` y la unidad migradora, y ejecutar
la migración. Después provisionar el principal web con el manifest de ese
`SHA`. Ambos environments resultantes son `root:root 0600` y contienen
únicamente los secretos de Oficina. Construir/atestiguar el par fuente
JAX/JXP del mismo SHA mediante el [procedimiento del par](despliegue.md),
pero adoptar el artefacto con el adoptador directamente. No ejecutar
`bootstrap-pair-predecessor.py` ni `activate-platform-pair-release.sh`:
ambos cambian el selector global de JAX. La Oficina copia el release verificado
a `/opt/axioma-office-pair` y publica una selección propia que el activador
de JAX no puede modificar. Es una instalación inicial idempotente; una versión
distinta exige una migración controlada de Oficina. Medir el host grantee del
puente Docker y ejecutar ambos pasos del provisionador instalado desde `SHA`:

```bash
# Después de crear request.json y recibir receipt.json con el supervisor
# según despliegue.md, adoptar solo los bytes sellados. Este comando no
# publica /etc/jax/platform-pair-selection.json ni cambia JAX/current.
JAX_SHA='SHA_JAX_EMPAREJADO'
PAIR_MANIFEST_SHA256='SHA256_DEL_MANIFIESTO_VERIFICADO'
JAX_SELECTION_BEFORE=$(sudo sha256sum /etc/jax/platform-pair-selection.json)
JAX_CURRENT_BEFORE=$(sudo readlink /opt/jax-platform-pair/current)
sudo /usr/local/libexec/adopt-pair-root.py \
  --source /ruta-de-jaxbuild/par \
  --destination "/opt/jax-platform-pair/releases/$SHA-$JAX_SHA" \
  --expected-manifest-sha256 "$PAIR_MANIFEST_SHA256" \
  --request /ruta-root/request.json --receipt /ruta-root/receipt.json
test "$(sudo sha256sum /etc/jax/platform-pair-selection.json)" = "$JAX_SELECTION_BEFORE"
test "$(sudo readlink /opt/jax-platform-pair/current)" = "$JAX_CURRENT_BEFORE"
```

Si la adopción o cualquiera de las dos comparaciones falla, detener la
instalación de Oficina.

```bash
MEASURED_HOST=$(sudo docker inspect --format '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}' axioma-office-mariadb)
test -n "$MEASURED_HOST"
test "$(sudo awk -F= '$1=="JAX_DB_GRANTEE_HOST" {print $2}' /etc/axioma-office/deployment.env)" = "$MEASURED_HOST"
PROVISION=/usr/local/libexec/axioma-office-db-runtime/provision-db-runtime.sh
sudo "$PROVISION" --plan --migrator-only --skip-os-identities \
  --env /etc/axioma-office/deployment.env --state-root /etc/axioma-office \
  --admin-transport /usr/local/libexec/axioma-office-mariadb-admin \
  --measured-grantee-host "$MEASURED_HOST"
sudo "$PROVISION" --migrator-only --skip-os-identities \
  --env /etc/axioma-office/deployment.env --state-root /etc/axioma-office \
  --admin-transport /usr/local/libexec/axioma-office-mariadb-admin \
  --measured-grantee-host "$MEASURED_HOST"
sudo /usr/local/libexec/axioma-office-pair-install \
  --source-release "/opt/jax-platform-pair/releases/$SHA-$JAX_SHA" \
  --expected-platform-sha "$SHA" --expected-jax-sha "$JAX_SHA" \
  --expected-manifest-sha256 "$PAIR_MANIFEST_SHA256"
sudo /usr/local/libexec/axioma-office-base-install \
  --source "$SOURCE" --revision "$SHA" --phase migration
sudo systemctl start axioma-office-migrate.service
sudo "$PROVISION" --plan --skip-os-identities \
  --env /etc/axioma-office/deployment.env --state-root /etc/axioma-office \
  --admin-transport /usr/local/libexec/axioma-office-mariadb-admin \
  --measured-grantee-host "$MEASURED_HOST"
sudo "$PROVISION" --skip-os-identities \
  --env /etc/axioma-office/deployment.env --state-root /etc/axioma-office \
  --admin-transport /usr/local/libexec/axioma-office-mariadb-admin \
  --measured-grantee-host "$MEASURED_HOST"
sudo /usr/local/libexec/axioma-office-base-install \
  --source "$SOURCE" --revision "$SHA" --phase services
sudo /usr/local/libexec/axioma-office-base-artifact-readiness "$SOURCE" "$SHA" --phase services
```

**Antes de arrancar el backend por primera vez**, importar la cuenta raíz.
El contrato de arranque exige al menos una cuenta en `jax_users`; invertir
este orden impide iniciar la API. Ejecutar los importadores con el Python del
par Office ya atestiguado, usando `/etc/jax/runtime.env` solo como fuente de
lectura y las credenciales Office propias como destino:

```bash
PAIR=/opt/axioma-office-pair/current/jax-platform
PYTHON="$PAIR/backend/.venv/bin/python"
OWNER_EMAIL='correo-exacto-del-superadministrador'
sudo "$PYTHON" -I -B "$PAIR/ops/virtualoffice/import-owner.py" \
  --source-env /etc/jax/runtime.env \
  --office-env /etc/axioma-office/migrate.env \
  --email "$OWNER_EMAIL"
sudo "$PYTHON" -I -B "$PAIR/ops/virtualoffice/import-smtp.py" \
  --source-env /etc/jax/runtime.env \
  --office-env /etc/axioma-office/runtime.env \
  --owner-email "$OWNER_EMAIL"
sudo systemctl start axioma-office-backend.service
sudo systemctl status axioma-office-database.service axioma-office-migrate.service axioma-office-backend.service
```

`import-owner.py` verifica `@@server_id` y `@@hostname` de ambos extremos y
copia una sola vez el hash actual de la cuenta superadministradora activa;
no copia sesiones, MFA ni proyectos. `import-smtp.py` recifra el secreto SMTP
con la clave Fernet nueva de Oficina. No imprimir secretos en logs ni scripts.
Comprobar login con la contraseña existente y luego el cambio de contraseña
dentro de Oficina; verificar que la cuenta de Axioma sigue validando su
contraseña anterior.

## 4. Correo y publicación

Instalar rutinas, cuenta `officeinvite`, release binario y timer **dedicados**
siguiendo [invitation-delivery-worker.md](invitation-delivery-worker.md). El
timer propio usa `/opt/axioma-office-dedicated-delivery` y su helper propio.
Probar conexión SMTP, emitir una invitación de prueba y confirmar recepción.
Luego construir/atestiguar el frontend del SHA y activar nginx según
[virtualoffice-public-ingress.md](virtualoffice-public-ingress.md), con upstream
al listener `axioma-office-backend` y rutas SMTP enumeradas. Medir carga de
login, directorio y proyectos con concurrencia real; registrar RPS y p95 en
la Biblioteca antes de publicar.
