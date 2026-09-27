#!/usr/bin/env bash
set -euo pipefail

# Guarda (o reemplaza) la línea CLAUDE_CODE_OAUTH_TOKEN= de /etc/jax/.env --
# la credencial de la cuenta Max de Fernando que necesita el sync de
# `anthropic` bajo `jaxsvc` (ver backend/model_catalog.py,
# ANTHROPIC_OAUTH_TOKEN_ENV). El valor sale de `claude setup-token`
# (Fernando, en su propia máquina) y se pega acá -- decisión explícita: NADA
# de API key de Anthropic.
#
# Se corre con sudo (edita un archivo 640 root:jaxsvc). Ruta configurable
# con JAX_ENV_PATH -- default /etc/jax/.env. NUNCA correr esto a mano contra
# el archivo real como prueba: backend/tests/test_guardar_token_claude_script.py
# lo ejercita contra un archivo falso en un tmpdir.
#
# NO reinicia ningún servicio -- lo dice al final, para que quien lo corre
# decida cuándo.

RUTA_ENV="${JAX_ENV_PATH:-/etc/jax/.env}"
VARIABLE="CLAUDE_CODE_OAUTH_TOKEN"
PREFIJO_ESPERADO="sk-ant-oat"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Corré esto con sudo -- edita un archivo que sólo root puede escribir." >&2
  exit 1
fi

if [[ ! -f "${RUTA_ENV}" ]]; then
  echo "No existe ${RUTA_ENV} -- nada que editar." >&2
  exit 1
fi

echo "Pegá el token de 'claude setup-token' (no se muestra en pantalla):" >&2
read -rs TOKEN
echo >&2

if [[ -z "${TOKEN}" ]]; then
  echo "Token vacío -- no se cambió nada." >&2
  exit 1
fi

if [[ "${TOKEN}" != "${PREFIJO_ESPERADO}"* ]]; then
  echo "El token no empieza con '${PREFIJO_ESPERADO}' -- ¿copiaste bien? No se cambió nada." >&2
  exit 1
fi

# Copia fechada ANTES de tocar nada, con los mismos permisos que el
# original (Regla del carpintero: medir dos veces, cortar una).
RESPALDO="${RUTA_ENV}.bak-$(date +%Y%m%d-%H%M%S)"
cp -p "${RUTA_ENV}" "${RESPALDO}"

DUENIO_ANTES="$(stat -c '%U:%G' "${RUTA_ENV}")"
MODO_ANTES="$(stat -c '%a' "${RUTA_ENV}")"

# Archivo temporal en el MISMO directorio que RUTA_ENV: el `mv` de más abajo
# es entonces un rename() atómico sobre el mismo filesystem, nunca una copia
# a medias si algo interrumpe el proceso.
TEMP="$(mktemp "${RUTA_ENV}.XXXXXX")"
trap 'rm -f "${TEMP}"' EXIT

# Reemplazo/agregado en bash puro (read/printf, sin sed ni awk como
# proceso aparte): el token NUNCA viaja como argumento de otro proceso --
# ni `ps` durante la corrida ni el historial de un `sed -e "...${TOKEN}..."`
# lo verían. `printf` y `read` son builtins de bash, no forkean.
encontrada=0
while IFS= read -r linea || [[ -n "${linea}" ]]; do
  if [[ "${linea}" == "${VARIABLE}="* ]]; then
    printf '%s=%s\n' "${VARIABLE}" "${TOKEN}"
    encontrada=1
  else
    printf '%s\n' "${linea}"
  fi
done < "${RUTA_ENV}" > "${TEMP}"

if [[ "${encontrada}" -eq 0 ]]; then
  printf '%s=%s\n' "${VARIABLE}" "${TOKEN}" >> "${TEMP}"
fi

# Dueño y modo del temporal = los del original, ANTES del mv -- así el
# archivo que aparece en destino ya nace correcto (nunca hay una ventana
# donde RUTA_ENV tenga el modo por defecto de `mktemp`, 600).
chown --reference="${RUTA_ENV}" "${TEMP}"
chmod --reference="${RUTA_ENV}" "${TEMP}"
mv -f "${TEMP}" "${RUTA_ENV}"
trap - EXIT

# Verificación final (el que supone se equivoca): confirma que el mv no
# cambió nada, en vez de asumir que chown/chmod/mv hicieron lo que debían.
DUENIO_DESPUES="$(stat -c '%U:%G' "${RUTA_ENV}")"
MODO_DESPUES="$(stat -c '%a' "${RUTA_ENV}")"

if [[ "${DUENIO_ANTES}" != "${DUENIO_DESPUES}" || "${MODO_ANTES}" != "${MODO_DESPUES}" ]]; then
  echo "ERROR: dueño/modo de ${RUTA_ENV} cambiaron (${DUENIO_ANTES}/${MODO_ANTES} -> ${DUENIO_DESPUES}/${MODO_DESPUES})." >&2
  echo "Restaurando desde ${RESPALDO}." >&2
  cp -p "${RESPALDO}" "${RUTA_ENV}"
  exit 1
fi

echo "OK: ${VARIABLE} actualizado en ${RUTA_ENV}. Respaldo en ${RESPALDO}." >&2
echo "Este script NO reinicia nada. Reiniciá a mano lo que lea esa credencial: systemctl restart jax-platform" >&2
