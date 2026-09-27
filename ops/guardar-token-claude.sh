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
# Anthropic).
# MAJOR-5 (segunda auditoría adversarial, 2026-09-27): el mínimo sube de 40 a
# 100 -- MEDIDO contra un token OAuth REAL "sk-ant-oat01-…" de la cuenta de
# Fernando, que mide 108. El máximo (400) es HOLGADO a propósito: el largo
# exacto que puede llegar a tener un token de `claude setup-token` no está
# medido, así que se pone un techo generoso en vez de uno ajustado que
# podría rechazar un token real más largo mañana.
PATRON_TOKEN='^sk-ant-oat01-[A-Za-z0-9_-]+$'
LARGO_MINIMO=100
LARGO_MAXIMO=400

# Vacía lo que quede en stdin sin leer y dice, por código de salida, si HABÍA
# algo -- un token pegado partido en dos líneas por el terminal deja la
# SEGUNDA línea esperando en la entrada. `read -t 0.1` en bucle consume todo
# lo que haya sin bloquear cuando ya no queda nada.
#
# MAJOR-5 (segunda auditoría adversarial, 2026-09-27): antes esta función
# sólo DRENABA el sobrante y el script seguía adelante con la primera línea
# como si fuera el token completo -- un token realmente partido en dos
# (copiado mal, con un salto de línea en el medio) se guardaba TRUNCADO, sin
# ningún aviso. Ahora, además de drenar (nunca se deja el resto sin leer:
# eso seguiría siendo un problema aparte, ver el comentario de más arriba),
# devuelve 0 si encontró algo -- el llamador aborta sin tocar el .env.
#
# MINOR-9 (cuarta auditoría adversarial, 2026-09-28): sólo cuenta como
# "sobrante" una línea con CONTENIDO -- una línea vacía de más (un Enter de
# más al pegar, o el propio "\n" final que separa el token de nada) no es un
# pegado partido, es ruido inofensivo. Se drena TODO igual (vacío o no),
# pero "encontrado" sólo se marca si alguna de esas líneas no está vacía.
hay_stdin_sobrante() {
  local sobrante encontrado=1
  while read -r -t 0.1 sobrante; do
    if [[ -n "${sobrante}" ]]; then
      encontrado=0
    fi
  done
  return "${encontrado}"
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

if [[ -z "${TOKEN}" ]]; then
  echo "Token vacío -- no se cambió nada." >&2
  exit 1
fi

if [[ ! "${TOKEN}" =~ ${PATRON_TOKEN} || "${#TOKEN}" -lt "${LARGO_MINIMO}" || "${#TOKEN}" -gt "${LARGO_MAXIMO}" ]]; then
  echo "El token no tiene la forma esperada ('sk-ant-oat01-' + entre ${LARGO_MINIMO} y ${LARGO_MAXIMO} caracteres [A-Za-z0-9_-]) -- ¿copiaste bien? No se cambió nada." >&2
  exit 1
fi

# Punto 6 (tercera auditoría adversarial, 2026-09-27): un segundo pegado, a
# confirmar contra el primero -- el drenaje de stdin y la regex de arriba
# cubren "se cortó a la mitad" o "tiene basura pegada", pero NINGUNO de los
# dos detecta un pegado partido que por casualidad sigue teniendo la FORMA
# de un token válido (dos tokens de cuentas distintas, uno viejo y uno
# nuevo pegados sin querer, etc.). Pedirlo dos veces cubre cualquier forma
# de "no es el que creía que pegué", no sólo la mitad cortada.
#
# El chequeo de "algo quedó esperando en stdin" se hace ACÁ, una sola vez,
# DESPUÉS de las dos lecturas -- no después de cada una por separado. Si se
# chequeara también inmediatamente después de la PRIMERA lectura, un pegado
# doble legítimo (el token y su confirmación, los dos ya en el buffer de
# entrada -- que es exactamente cómo llega cuando esto se prueba con un pipe,
# o cuando alguien pega las dos líneas de una sola vez) se vería IDÉNTICO a
# un token partido: la confirmación, todavía sin leer, sería indistinguible
# de "sobró algo". Con el chequeo único después de ambas lecturas, un pegado
# partido de la PRIMERA línea tampoco se pierde: la mitad que sobra se
# termina leyendo como si fuera la confirmación, y casi con certeza NO va a
# coincidir con la primera mitad -- así que igual aborta, por "no coinciden"
# en vez de por "partido", pero sin tocar el .env en ningún caso.
echo "Volvé a pegar el MISMO token, para confirmar (tampoco se muestra):" >&2
read -rs TOKEN_CONFIRMACION
echo >&2

if hay_stdin_sobrante; then
  echo "El pegado llegó partido en más de dos líneas (quedaba algo más esperando en la entrada) -- volvé a pegar el token y su confirmación de nuevo, cada uno en una sola línea. No se cambió nada." >&2
  exit 1
fi

if [[ "${TOKEN}" != "${TOKEN_CONFIRMACION}" ]]; then
  echo "Los dos tokens no coinciden -- no se cambió nada. Volvé a correr el script." >&2
  exit 1
fi

# Copia fechada ANTES de tocar nada, con los mismos permisos que el
# original (Regla del carpintero: medir dos veces, cortar una).
RESPALDO="${RUTA_ENV}.bak-$(date +%Y%m%d-%H%M%S)"
cp -p "${RUTA_ENV}" "${RESPALDO}"

# A-8b/MINOR-6 (segunda auditoría adversarial, 2026-09-27): sólo se
# conservan los 2 respaldos MÁS RECIENTES que ESTE script haya creado.
#
# El glob es ESTRICTO -- exactamente el patrón de `date +%Y%m%d-%H%M%S`
# (8 dígitos, guion, 6 dígitos), no `bak-*` a secas. Un `*` a secas
# consideraba candidato a CUALQUIER archivo con ese prefijo, incluido uno
# ajeno puesto a mano con otro sufijo (`fake.env.bak-antes-rotar`,
# `fake.env.bak-1`) -- y si ese sufijo ordenaba antes que una fecha real
# ('1' < '2...', por ejemplo), el `head -n -2` de la versión anterior
# TERMINABA BORRÁNDOLO. El glob estricto ni siquiera lo lista.
#
# El respaldo RECIÉN CREADO (`RESPALDO`) se excluye EXPLÍCITAMENTE de la
# lista de candidatos (no se confía sólo en que el orden alfabético lo deje
# último): lo que queda son los viejos, y de ÉSOS se borran todos menos el
# más reciente (`head -n -1`) -- total conservado: el nuevo + 1 viejo = 2.
mapfile -t _RESPALDOS_VIEJOS < <(
  ls -1 "${RUTA_ENV}".bak-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9] 2>/dev/null \
    | grep -F -x -v -- "${RESPALDO}" | sort | head -n -1
)
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
