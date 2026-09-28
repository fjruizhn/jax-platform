#!/usr/bin/env bash
set -euo pipefail

# ops/desplegar-sin-ventana-de-sync.sh -- despliega un cambio que toca el
# nombre del candado (jax_catalogo_sync) o el ejecutor programado del
# catálogo de modelos (catalogo_modelos_ejecutor.py) SIN la ventana de dos
# syncs concurrentes que un `git pull` + reinicio "normal" dejaría abierta
# (2026-09-27, séptima ronda de la auditoría adversarial -- DECISIÓN del
# coordinador: los pasos que antes eran bloques de bash para pegar a mano
# en docs/runbooks/despliegue.md pasan a este guion versionado, probado).
#
# USO:
#   sudo bash ops/desplegar-sin-ventana-de-sync.sh <SHA-esperado-tras-el-fetch>
#
# El SHA esperado es OBLIGATORIO -- se compara contra el HEAD real del
# checkout de producción después del `fetch`+`merge --ff-only`, ANTES de
# reiniciar nada. Si no coincide, el guion aborta sin tocar el servicio.
#
# BARRERA DE ESTA TAREA: este guion se prueba con `systemctl`/`mariadb`/
# `git`/`curl`/`install`/`systemd-analyze` SIMULADOS (ver
# backend/tests/test_desplegar_sin_ventana_de_sync.py) -- NUNCA se ejecuta
# contra producción como parte de escribirlo o probarlo.
#
# DOS ZONAS, DOS MENSAJES DE ABORTO. Antes de reiniciar jax-platform (pasos
# 1-4), cualquier fallo aborta con "ABORTADO: no se reinició nada" -- nada
# quedó a medio camino. DESPUÉS de reiniciar jax-platform (pasos 5-7), un
# fallo ya no puede decir "no se reinició nada" (jax-platform SÍ corre con
# el checkout nuevo) -- avisa qué falta a mano en vez de mentir.

SHA_ESPERADO="${1:?Uso: sudo bash $0 <SHA-esperado-tras-el-fetch>}"

UNIDAD_SERVICIO="jax-catalogo-modelos.service"
UNIDAD_TIMER="jax-catalogo-modelos.timer"
UNIDAD_AVISO="jax-catalogo-modelos-aviso.service"
CHECKOUT="${JAX_CHECKOUT_PLATAFORMA:-/srv/jax-prod/jax-platform}"
DIR_UNIDADES="$CHECKOUT/ops/migration/systemd-units"
ENV_PATH="${JAX_ENV_PATH:-/etc/jax/.env}"
DUENIO_CHECKOUT="${JAX_DUENIO_CHECKOUT:-fruiz}"
DESTINO_UNIDADES="${JAX_DESTINO_UNIDADES:-/etc/systemd/system}"
URL_HEALTH="${JAX_URL_HEALTH:-http://127.0.0.1:8080/api/health}"
TOPE_ESPERA_SERVICIO_SEGUNDOS="${TOPE_ESPERA_SERVICIO_SEGUNDOS:-1900}"
TOPE_ESPERA_HEALTH_SEGUNDOS="${TOPE_ESPERA_HEALTH_SEGUNDOS:-60}"

abortar() {
    echo "ABORTADO: no se reinició nada -- $1" >&2
    exit 1
}

abortar_tras_reinicio() {
    echo "ABORTADO DESPUÉS de reiniciar jax-platform: $1" >&2
    echo "jax-platform YA corre con el checkout nuevo (${SHA_ACTUAL:-desconocido}) -- completar a mano desde acá, ver docs/runbooks/despliegue.md." >&2
    exit 1
}

echo "== 1/7: parando $UNIDAD_TIMER =="
systemctl stop "$UNIDAD_TIMER" || abortar "no se pudo parar $UNIDAD_TIMER"

echo "== 2/7: esperando que $UNIDAD_SERVICIO termine (si estaba corriendo) =="
# `Type=oneshot` SIN `RemainAfterExit`: mientras corre, ActiveState es
# "activating", NUNCA "active" -- comparar contra "active" pasa de largo
# con una corrida real en curso (hallazgo de la ronda anterior). Se espera
# a "inactive" o "failed" Y a que no queden trabajos encolados para esta
# unidad (un `stop` del timer no cancela un `start` ya en cola).
esperado=0
while :; do
    estado=$(systemctl show -p ActiveState --value "$UNIDAD_SERVICIO")
    trabajos_pendientes=1
    if [ -z "$(systemctl list-jobs --no-legend "$UNIDAD_SERVICIO" 2>/dev/null || true)" ]; then
        trabajos_pendientes=0
    fi
    case "$estado" in
        inactive|failed)
            if [ "$trabajos_pendientes" -eq 0 ]; then
                break
            fi
            ;;
    esac
    if [ "$esperado" -ge "$TOPE_ESPERA_SERVICIO_SEGUNDOS" ]; then
        abortar "$UNIDAD_SERVICIO sigue en '$estado' (trabajos encolados: $([ "$trabajos_pendientes" -eq 0 ] && echo no || echo si)) tras ${TOPE_ESPERA_SERVICIO_SEGUNDOS}s -- revisar con journalctl antes de reintentar"
    fi
    sleep 5
    esperado=$((esperado + 5))
done
echo "$UNIDAD_SERVICIO: $estado, sin trabajos encolados"

echo "== 3/7: freno autoritativo -- candados libres, sin sync manual corriendo =="
# Credenciales SIEMPRE por archivo de opciones 600 efímero -- nunca -p en
# argv (quedaría legible en /proc/<pid>/cmdline mientras corre la consulta).
set -a
# shellcheck disable=SC1090
. "$ENV_PATH"
set +a
BASE="${JAX_DB_NAME:-jax_memory}"
T=$(mktemp -d)
chmod 700 "$T"
trap 'rm -rf "$T"' EXIT
printf '[client]\nhost=%s\nport=%s\nuser=%s\npassword=%s\n' \
    "$JAX_DB_HOST" "$JAX_DB_PORT" "$JAX_DB_USER" "$JAX_DB_PASSWORD" > "$T/c.cnf"
chmod 600 "$T/c.cnf"
MARIADB=(mariadb --defaults-extra-file="$T/c.cnf" -N "$BASE")

# Los DOS nombres -- el viejo sin calificar y el nuevo calificado con la
# base -- porque un proceso con el código VIEJO (de antes de este cambio)
# todavía podría sostener el primero, y uno con el código NUEVO el
# segundo. Los dos tienen que dar NULL (nadie los sostiene) para seguir.
# Comillas SIMPLES en el SQL: válidas para strings sea cual sea el modo
# ANSI_QUOTES de la sesión (con ANSI_QUOTES activo, las comillas dobles
# dejan de ser strings y pasan a ser identificadores).
if ! candados=$("${MARIADB[@]}" -e \
    "SELECT IS_USED_LOCK('jax_catalogo_sync'), IS_USED_LOCK(CONCAT('jax_catalogo_sync:', DATABASE()))" \
    2>"$T/candados.err"); then
    abortar "no se pudo consultar los candados: $(cat "$T/candados.err")"
fi
read -r candado_viejo candado_nuevo <<<"$candados"
if [ "$candado_viejo" != "NULL" ] || [ "$candado_nuevo" != "NULL" ]; then
    abortar "al menos un candado sigue tomado (viejo='$candado_viejo' nuevo='$candado_nuevo') -- un sync está corriendo AHORA MISMO"
fi
echo "candados libres: jax_catalogo_sync=NULL, calificado=NULL"

if ! existe_tabla=$("${MARIADB[@]}" -e \
    "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='catalogo_sync_ejecucion'" \
    2>"$T/existe.err"); then
    abortar "no se pudo consultar information_schema.TABLES: $(cat "$T/existe.err")"
fi
if ! [[ "$existe_tabla" =~ ^[0-9]+$ ]]; then
    abortar "respuesta inesperada consultando information_schema.TABLES: '$existe_tabla'"
fi

if [ "$existe_tabla" -eq 1 ]; then
    if ! corriendo=$("${MARIADB[@]}" -e \
        "SELECT COUNT(*) FROM catalogo_sync_ejecucion WHERE estado='corriendo'" \
        2>"$T/corriendo.err"); then
        abortar "no se pudo consultar catalogo_sync_ejecucion: $(cat "$T/corriendo.err")"
    fi
    if ! [[ "$corriendo" =~ ^[0-9]+$ ]]; then
        abortar "respuesta inesperada consultando catalogo_sync_ejecucion: '$corriendo'"
    fi
    if [ "$corriendo" -ne 0 ]; then
        abortar "hay $corriendo fila(s) 'corriendo' en catalogo_sync_ejecucion -- un sync manual sigue en curso DENTRO de jax-platform"
    fi
    echo "catalogo_sync_ejecucion: 0 filas corriendo"
else
    echo "catalogo_sync_ejecucion: la tabla todavía no existe (primer despliegue de esta función) -- nada que revisar ahí"
fi
rm -rf "$T"
trap - EXIT

echo "== 4/7: actualizando el checkout de producción ($CHECKOUT) =="
sudo -u "$DUENIO_CHECKOUT" git -C "$CHECKOUT" -c safe.directory="$CHECKOUT" fetch origin \
    || abortar "git fetch falló en $CHECKOUT"
sudo -u "$DUENIO_CHECKOUT" git -C "$CHECKOUT" -c safe.directory="$CHECKOUT" merge --ff-only origin/master \
    || abortar "git merge --ff-only falló en $CHECKOUT -- ¿el checkout tiene cambios locales o divergió de origin/master?"

SHA_ACTUAL=$(sudo -u "$DUENIO_CHECKOUT" git -C "$CHECKOUT" -c safe.directory="$CHECKOUT" rev-parse HEAD)
if [ "$SHA_ACTUAL" != "$SHA_ESPERADO" ]; then
    abortar "el SHA tras el merge ($SHA_ACTUAL) no coincide con el esperado ($SHA_ESPERADO) -- revisar antes de reiniciar nada"
fi
echo "checkout en $SHA_ACTUAL (coincide con el esperado)"

echo "== 5/7: reiniciando jax-platform -- PUNTO DE NO RETORNO de este guion =="
systemctl restart jax-platform || abortar_tras_reinicio "no se pudo reiniciar jax-platform"

esperado=0
while :; do
    codigo=$(curl -s -o /dev/null -w '%{http_code}' "$URL_HEALTH" 2>/dev/null || echo "000")
    if [ "$codigo" = "200" ]; then
        break
    fi
    if [ "$esperado" -ge "$TOPE_ESPERA_HEALTH_SEGUNDOS" ]; then
        abortar_tras_reinicio "$URL_HEALTH no dio 200 tras ${TOPE_ESPERA_HEALTH_SEGUNDOS}s (último código: $codigo)"
    fi
    sleep 2
    esperado=$((esperado + 2))
done
echo "$URL_HEALTH: 200"

echo "== 6/7: verificando e instalando las 3 unidades =="
# Se VERIFICAN LAS TRES antes de instalar CUALQUIERA -- si la unidad #2
# tiene un error de sintaxis, la #1 no queda instalada a medias.
for U in "$UNIDAD_SERVICIO" "$UNIDAD_TIMER" "$UNIDAD_AVISO"; do
    systemd-analyze verify "$DIR_UNIDADES/$U" \
        || abortar_tras_reinicio "systemd-analyze verify falló para $U -- NO se instaló ninguna unidad"
done
for U in "$UNIDAD_SERVICIO" "$UNIDAD_TIMER" "$UNIDAD_AVISO"; do
    install -m 644 -o root -g root "$DIR_UNIDADES/$U" "$DESTINO_UNIDADES/$U" \
        || abortar_tras_reinicio "no se pudo instalar $U"
done
systemctl daemon-reload || abortar_tras_reinicio "daemon-reload falló"
echo "las 3 unidades verificadas, instaladas, daemon-reload hecho"

echo "== 7/7: arrancando $UNIDAD_TIMER =="
systemctl start "$UNIDAD_TIMER" || abortar_tras_reinicio "no se pudo arrancar $UNIDAD_TIMER"
systemctl list-timers "$UNIDAD_TIMER"

cat <<EOF

Backend y ejecutor programado desplegados (checkout en $SHA_ACTUAL). TODAVÍA
FALTA -- fuera del alcance de este guion, seguir con
docs/runbooks/despliegue.md:
  - Sección 3: frontend interno (:5173)
  - Sección 4: sitio público (axioma-ia.io)
  - Sección 5: verificar desde afuera
  - Verificación EJECUTABLE de esta sección: forzar una corrida real (el
    botón "Sincronizar", como superadmin) y confirmar, mientras corre,
    que IS_USED_LOCK(...) NO es NULL -- y que la fila de
    catalogo_sync_ejecucion cierra 'ok' o 'con_problemas', nunca 'error'.
EOF
