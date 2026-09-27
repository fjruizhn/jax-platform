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
# A-8a (auditoría adversarial, 2026-09-27): el prefijo solo ("sk-ant-oat")
# no alcanzaba -- aceptaba cualquier basura después. El patrón real de
# Claude Code es "sk-ant-oat01-" + el cuerpo del token, alfabeto
# [A-Za-z0-9_-] (el mismo que usan las demás credenciales tipo API-key de
# Anthropic). 40 es un mínimo conservador: un token real mide bastante más,
# pero cualquier prueba de humo con menos que eso es obviamente basura.
PATRON_TOKEN='^sk-ant-oat01-[A-Za-z0-9_-]+$'
LARGO_MINIMO=40

# (a, segunda mitad) Vacía lo que quede en stdin sin leer -- un token pegado
# partido en dos líneas por el terminal deja la SEGUNDA línea esperando en
# la entrada; sin drenarla, un `sudo bash guardar-token-claude.sh` corrido
# pegado a una terminal real dejaría esa línea para que el shell que lanzó
# este script la lea como si fuera SU propio comando siguiente. `read -t 0.1`
# en bucle consume todo lo que haya sin bloquear cuando ya no queda nada.
vaciar_stdin_sobrante() {
  local sobrante
  while read -r -t 0.1 sobrante; do :; done
}

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
vaciar_stdin_sobrante

if [[ -z "${TOKEN}" ]]; then
  echo "Token vacío -- no se cambió nada." >&2
  exit 1
fi

if [[ ! "${TOKEN}" =~ ${PATRON_TOKEN} || "${#TOKEN}" -lt "${LARGO_MINIMO}" ]]; then
  echo "El token no tiene la forma esperada ('sk-ant-oat01-' + al menos ${LARGO_MINIMO} caracteres [A-Za-z0-9_-]) -- ¿copiaste bien? No se cambió nada." >&2
  exit 1
fi

# Copia fechada ANTES de tocar nada, con los mismos permisos que el
# original (Regla del carpintero: medir dos veces, cortar una).
RESPALDO="${RUTA_ENV}.bak-$(date +%Y%m%d-%H%M%S)"
cp -p "${RUTA_ENV}" "${RESPALDO}"

# A-8b: sólo se conservan los 2 respaldos MÁS RECIENTES que ESTE script haya
# creado -- el patrón de nombre es el propio (`<RUTA_ENV>.bak-*`), así que
# nunca borra un archivo de respaldo ajeno que viva en el mismo directorio.
# El sello de `date +%Y%m%d-%H%M%S` ordena igual alfabético que cronológico.
mapfile -t _RESPALDOS_VIEJOS < <(ls -1 "${RUTA_ENV}".bak-* 2>/dev/null | sort | head -n -2)
if [[ "${#_RESPALDOS_VIEJOS[@]}" -gt 0 ]]; then
  rm -f -- "${_RESPALDOS_VIEJOS[@]}"
fi

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
# (c, auditoría adversarial 2026-09-27): jax-platform corre el sync del
# catálogo (model_catalog.py) que lee esta variable; jax-las-manos carga el
# MISMO /etc/jax/.env al arrancar (EnvironmentFile= no se relee en caliente)
# -- los dos quedan con el token viejo en memoria hasta que se reinician,
# se listan los dos a propósito.
echo "Este script NO reinicia nada. Reiniciá a mano: systemctl restart jax-platform jax-las-manos" >&2
