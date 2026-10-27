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
únicamente los secretos de Oficina. La activación del par JAX/JXP del mismo
SHA se realiza con el procedimiento de [despliegue del par](despliegue.md),
incluida su selección firmada/atestada; el gate siguiente rechaza otra
revisión activa. Medir el host grantee del puente Docker y ejecutar ambos
pasos del provisionador instalado desde `SHA`:

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
sudo systemctl start axioma-office-backend.service
sudo systemctl status axioma-office-database.service axioma-office-migrate.service axioma-office-backend.service
```

Antes del primer login, ejecutar `ops/virtualoffice/import-owner.py` una
sola vez desde el release aprobado con credenciales de lectura fuente y
escritura Office distintas. Verifica `@@server_id` y `@@hostname` de ambos
extremos y copia únicamente el hash actual de la cuenta superadministradora
activa; no copia sesiones, MFA ni proyectos. Ejecutar después
`ops/virtualoffice/import-smtp.py`, que recifra el secreto SMTP con la clave
Fernet nueva de Oficina. No imprimir secretos en logs ni scripts. Comprobar
login con la contraseña existente y luego el cambio de contraseña dentro de
Oficina; verificar que la cuenta de Axioma sigue validando su contraseña
anterior.

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
