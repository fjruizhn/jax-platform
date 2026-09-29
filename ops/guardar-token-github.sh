#!/usr/bin/env bash
set -euo pipefail

# Guarda (o reemplaza) la línea JAX_GITHUB_TOKEN= de /etc/jax/.env -- la
# credencial que el Ejecutor usa para entregar código (Task 12, spec
# 2026-09-28 v1.3, "El Ejecutor programa"). Lo consume
# jax/ejecutor/mision_servicio.py (VARIABLE_TOKEN), corrido como subproceso
# de jax-platform con `entorno = dict(os.environ)`
# (ejecutor/misiones.py::_runner): el token tiene que estar en el entorno
# del PROPIO jax-platform, no en el de jax-las-manos.
#
# El valor es un Personal Access Token de GitHub FINE-GRAINED
# ("github_pat_..."), generado por Fernando en GitHub y pegado acá --
# decisión explícita (DC3): NUNCA un token clásico ("ghp_..."), que no
# admite el alcance acotado por repo que exige el contrato de arranque.
#
# Se corre con sudo (edita un archivo 640 root:jaxsvc). Ruta configurable
# con JAX_ENV_PATH -- default /etc/jax/.env. NUNCA correr esto a mano contra
# el archivo real como prueba: backend/tests/test_guardar_token_github_script.py
# lo ejercita contra un archivo falso en un tmpdir.
#
# NO reinicia ningún servicio -- lo dice al final, para que quien lo corre
# decida cuándo.

RUTA_ENV="${JAX_ENV_PATH:-/etc/jax/.env}"
VARIABLE="JAX_GITHUB_TOKEN"
# Fine-grained PAT de GitHub: prefijo "github_pat_" + el cuerpo, alfabeto
# [A-Za-z0-9_] (sin guion -- a diferencia del token OAuth de Claude, GitHub
# no lo usa en el cuerpo de un fine-grained PAT).
PATRON_TOKEN='^github_pat_[A-Za-z0-9_]+$'
LARGO_MINIMO=40
LARGO_MAXIMO=255

# Vacía lo que quede en stdin sin leer y dice, por código de salida, si HABÍA
# algo -- un token pegado partido en dos líneas por el terminal deja la
# SEGUNDA línea esperando en la entrada. `read -t 0.1` en bucle consume todo
# lo que haya sin bloquear cuando ya no queda nada.
#
# Sólo cuenta como "sobrante" una línea con CONTENIDO -- una línea vacía de
# más (un Enter de más al pegar, o el propio "\n" final que separa el token
# de nada) no es un pegado partido, es ruido inofensivo. Se drena TODO igual
# (vacío o no), pero "encontrado" sólo se marca si alguna de esas líneas no
# está vacía.
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

echo "Pegá el Personal Access Token fine-grained de GitHub (no se muestra en pantalla):" >&2
read -rs TOKEN
echo >&2

if [[ -z "${TOKEN}" ]]; then
  echo "Token vacío -- no se cambió nada." >&2
  exit 1
fi

if [[ ! "${TOKEN}" =~ ${PATRON_TOKEN} || "${#TOKEN}" -lt "${LARGO_MINIMO}" || "${#TOKEN}" -gt "${LARGO_MAXIMO}" ]]; then
  echo "El token no tiene la forma esperada ('github_pat_' + entre ${LARGO_MINIMO} y ${LARGO_MAXIMO} caracteres [A-Za-z0-9_]) -- ¿copiaste bien, o es un token clásico ('ghp_...')? DC3 exige grano fino. No se cambió nada." >&2
  exit 1
fi

# Pedirlo dos veces, a confirmar contra el primero -- el drenaje de stdin y
# la regex de arriba cubren "se cortó a la mitad" o "tiene basura pegada",
# pero NINGUNO de los dos detecta un pegado partido que por casualidad sigue
# teniendo la FORMA de un token válido (dos tokens de cuentas distintas, uno
# viejo y uno nuevo pegados sin querer, etc.). Pedirlo dos veces cubre
# cualquier forma de "no es el que creía que pegué", no sólo la mitad
# cortada.
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

# Sólo se conservan los 2 respaldos MÁS RECIENTES que ESTE script haya
# creado.
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
# jax-platform corre el runner del Ejecutor (jax/ejecutor/mision_servicio.py)
# como subproceso, heredando SU PROPIO entorno (EnvironmentFile= no se relee
# en caliente) -- queda con el token viejo en memoria hasta que se reinicia.
echo "Este script NO reinicia nada. Reiniciá a mano: systemctl restart jax-platform" >&2
