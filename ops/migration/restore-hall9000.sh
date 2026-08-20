#!/usr/bin/env bash
#
# restore-hall9000.sh — Restauración automatizada del ecosistema JAX / Axioma
# ---------------------------------------------------------------------------
# Se ejecuta EN LA MÁQUINA DESTINO (hall9000, Ubuntu 26.04, usuario fruiz)
# para restaurar desde el paquete de migración.
#
# Protocolo Hyde:
#   - Idempotente donde es posible (re-ejecutar no rompe lo ya hecho).
#   - Ningún paso destructivo sin confirmación explícita.
#   - Checkpoints de verificación entre fases mayores.
#   - La passphrase y el .env NUNCA se escriben en el script ni en logs.
#
# Uso:
#   bash restore-hall9000.sh --dry-run   # ← PRIMERA PASADA RECOMENDADA
#                                          muestra qué haría cada fase, sin tocar nada.
#   bash restore-hall9000.sh             # ejecución real, con checkpoints.
#
#   Var opcional AUTO_CONTINUE=1  → salta SOLO las pausas informativas
#                                   (las confirmaciones destructivas SIEMPRE preguntan).
#
set -uo pipefail

# ───────────────────────────── Argumentos ─────────────────────────────
DRY_RUN=0
usage() {
  cat <<USAGE
restore-hall9000.sh — restaura JAX/Axioma desde el paquete de migración.

  --dry-run, -n   Simula: imprime exactamente qué haría cada fase (clones, imports,
                  descifrado, venvs, pulls, units) SIN ejecutar ni tocar nada.
                  Es la forma recomendada para la primera pasada.
  -h, --help      Esta ayuda.

La Fase 0 (verificación de prerequisitos) SÍ se ejecuta en --dry-run porque es
de solo lectura; el resto de fases solo se describe.
USAGE
}
for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY_RUN=1 ;;
    -h|--help)    usage; exit 0 ;;
    *) printf 'Argumento desconocido: %s\n\n' "$arg" >&2; usage >&2; exit 2 ;;
  esac
done

# ───────────────────────────── Configuración ─────────────────────────────
MIG_DIR="${MIG_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

JAX_DIR="/home/fruiz/jax"
PLATFORM_DIR="/home/fruiz/jax-platform"
NODE_DIR="/home/fruiz/.nvm/versions/node/v24.16.0"
NODE_BIN="$NODE_DIR/bin"
EXPECTED_NODE="v24.16.0"

REPO_JAX="git@github.com:fjruizhn/Jax.git"
REPO_PLATFORM="git@github.com:fjruizhn/jax-platform.git"

DB_NAME="jax_memory"
DB_DUMP="$MIG_DIR/jax_memory-20260708.sql.gz"
ENV_ENC="$MIG_DIR/jax.env.enc"
ENV_DEST="/etc/jax/.env"
UNITS_DIR="$MIG_DIR/systemd-units"

# venv → requirements  (label|ruta_venv|ruta_requirements)
VENVS=(
  "las_manos|$JAX_DIR/las_manos/.venv|$JAX_DIR/requirements.txt"
  "memory-worker|$JAX_DIR/.venv|$JAX_DIR/requirements.txt"
  "axioma-backend|$PLATFORM_DIR/backend/.venv|$PLATFORM_DIR/backend/requirements.txt"
)

OLLAMA_MODELS=(nomic-embed-text qwen3:14b qwen2.5:7b llama3.2:3b)

AUTO_CONTINUE="${AUTO_CONTINUE:-0}"

# ───────────────────────────── Utilidades ─────────────────────────────
c_reset=$'\033[0m'; c_bold=$'\033[1m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_red=$'\033[31m'; c_blu=$'\033[36m'; c_mag=$'\033[35m'
log()  { printf '%s\n' "$*"; }
info() { printf '%s→%s %s\n' "$c_blu" "$c_reset" "$*"; }
ok()   { printf '%s✓%s %s\n' "$c_grn" "$c_reset" "$*"; }
warn() { printf '%s⚠%s %s\n' "$c_ylw" "$c_reset" "$*"; }
err()  { printf '%s✗%s %s\n' "$c_red" "$c_reset" "$*" >&2; }
die()  { err "$*"; err "Abortado. Nada más se ejecutó."; exit 1; }

is_dry() { [ "$DRY_RUN" = "1" ]; }
would()  { printf '  %s[DRY]%s %s\n' "$c_mag" "$c_reset" "$*"; }   # qué haría en real

phase() {
  printf '\n%s══════════════════════════════════════════════════════════%s\n' "$c_bold" "$c_reset"
  printf '%s  FASE %s%s\n' "$c_bold" "$*" "$c_reset"
  printf '%s══════════════════════════════════════════════════════════%s\n' "$c_bold" "$c_reset"
}

# confirm "pregunta" → 0 si el usuario responde 's'. Nunca se salta con AUTO_CONTINUE.
# En --dry-run no pregunta: informa que preguntaría y devuelve "no" (no simula el destructivo).
confirm() {
  local q="$1" ans
  if is_dry; then would "en real preguntaría: «$q»"; return 1; fi
  printf '%s? %s [s/N]: ' "$c_ylw" "$q$c_reset" > /dev/tty
  read -r ans < /dev/tty || ans=""
  [[ "$ans" =~ ^[sSyY]$ ]]
}

# Pausa informativa entre fases. Se salta en --dry-run y con AUTO_CONTINUE=1.
checkpoint() {
  local msg="${1:-Revisa el resultado de la fase anterior.}"
  is_dry && return 0
  if [ "$AUTO_CONTINUE" = "1" ]; then info "(AUTO_CONTINUE) continuando sin pausa…"; return 0; fi
  printf '\n%s— CHECKPOINT —%s %s\n' "$c_bold" "$c_reset" "$msg" > /dev/tty
  printf '  [Enter] continuar   ·   [Ctrl-C] abortar\n' > /dev/tty
  read -r _ < /dev/tty || true
}

need_cmd() { command -v "$1" >/dev/null 2>&1; }

# Banner de modo
if is_dry; then
  printf '%s╔══════════════════════════════════════════════════════════╗%s\n' "$c_mag" "$c_reset"
  printf '%s║  MODO --dry-run · SIMULACIÓN — no se ejecuta ni toca nada  ║%s\n' "$c_mag" "$c_reset"
  printf '%s║  (la Fase 0 sí verifica prerequisitos: es de solo lectura) ║%s\n' "$c_mag" "$c_reset"
  printf '%s╚══════════════════════════════════════════════════════════╝%s\n' "$c_mag" "$c_reset"
fi

# ───────────────────────────── FASE 0: Prerequisitos (read-only, corre en ambos modos) ────────────
phase "0 · Verificación de prerequisitos"
FAIL=0

if [ "$(id -un)" != "fruiz" ]; then
  warn "Ejecutándose como '$(id -un)', se esperaba 'fruiz'. Las rutas /home/fruiz/... podrían no corresponder."
fi
[ "$(id -u)" = "0" ] && die "No ejecutes este script como root. Córrelo como 'fruiz' (usa sudo internamente cuando hace falta)."

info "Paquete de migración: $MIG_DIR"
for f in "$DB_DUMP" "$ENV_ENC" "$UNITS_DIR"; do
  [ -e "$f" ] && ok "presente: $(basename "$f")" || { err "falta en el paquete: $f"; FAIL=1; }
done

for c in git openssl python3 sudo; do
  if need_cmd "$c"; then ok "$c: $(command -v "$c")"; else err "falta comando: $c"; FAIL=1; fi
done
need_cmd git && info "git $(git --version | awk '{print $3}')"

if need_cmd mariadb; then MYSQL_CLI="mariadb"; ok "cliente MariaDB: mariadb"
elif need_cmd mysql; then MYSQL_CLI="mysql"; ok "cliente MariaDB: mysql"
else err "falta cliente MariaDB (mariadb/mysql)"; FAIL=1; MYSQL_CLI="mariadb"; fi

if sudo "$MYSQL_CLI" -e "SELECT VERSION()" >/dev/null 2>&1; then
  ok "MariaDB server accesible: $(sudo "$MYSQL_CLI" -N -e 'SELECT VERSION()' 2>/dev/null)"
else
  err "No se pudo conectar a MariaDB por socket (sudo $MYSQL_CLI). ¿El servicio mariadb está activo?"; FAIL=1
fi

if need_cmd ollama && ollama list >/dev/null 2>&1; then ok "ollama operativo"; else err "ollama no responde (¿daemon arriba?)"; FAIL=1; fi

if [ -x "$NODE_BIN/node" ]; then
  NV="$("$NODE_BIN/node" --version 2>/dev/null)"
  if [ "$NV" = "$EXPECTED_NODE" ]; then ok "node $NV en $NODE_BIN"
  else warn "node en $NODE_BIN es $NV, se esperaba $EXPECTED_NODE"; fi
else err "no existe $NODE_BIN/node (nvm v24.16.0)"; FAIL=1; fi

# Nota: ssh -T a GitHub sale con exit 1 (no da shell access) — es normal. Con
# `pipefail`, un pipe `ssh | grep` propagaría ese 1 aunque grep sí matchee, dando
# un falso negativo. Por eso capturamos la salida y evaluamos la cadena, sin pipe.
GH_OUT="$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -T git@github.com 2>&1)"
if [[ "$GH_OUT" == *"successfully authenticated"* ]]; then
  ok "SSH a GitHub autenticado"
else
  err "SSH a GitHub no autenticado. Registra la clave pública de esta máquina en GitHub (cuenta fjruizhn) antes de continuar."; FAIL=1
fi

if [ "$FAIL" -eq 0 ]; then
  ok "Todos los prerequisitos presentes."
elif is_dry; then
  warn "Faltan prerequisitos (ver ✗). En ejecución REAL se abortaría aquí; en --dry-run continúo para mostrarte el plan completo."
else
  die "Faltan prerequisitos (ver ✗ arriba). Corrígelos y re-ejecuta."
fi
checkpoint "Prerequisitos OK. ¿Continuar con la clonación de repos?"

# ───────────────────────────── FASE 1: Clonar repos ─────────────────────────────
phase "1 · Clonar repositorios desde GitHub"

clone_repo() {
  local repo="$1" dir="$2" name="$3"
  if is_dry; then
    if [ -d "$dir/.git" ]; then
      local rr; rr="$(git -C "$dir" remote get-url origin 2>/dev/null || echo '?')"
      would "$name: ya clonado en $dir (remote: $rr) → ofrecería 'git pull --ff-only'"
    elif [ -e "$dir" ]; then
      would "$name: $dir existe y NO es repo git → NO lo tocaría"
    else
      would "$name: git clone $repo → $dir"
    fi
    return 0
  fi
  if [ -d "$dir/.git" ]; then
    local remote; remote="$(git -C "$dir" remote get-url origin 2>/dev/null || echo '')"
    if [ "$remote" = "$repo" ]; then
      ok "$name ya clonado en $dir (remote correcto)."
      if confirm "$name: ¿hacer 'git pull --ff-only' para actualizar?"; then
        git -C "$dir" fetch --all --prune && git -C "$dir" pull --ff-only || warn "pull no fast-forward: revisa manualmente."
      else info "conservando estado actual de $name."; fi
    else
      warn "$dir ya es un repo git pero con OTRO remote ($remote). No lo toco."
    fi
  elif [ -e "$dir" ]; then
    warn "$dir ya existe y NO es un repo git. No lo sobrescribo — revísalo manualmente."
  else
    info "clonando $name → $dir"
    git clone "$repo" "$dir" && ok "$name clonado." || die "fallo clonando $name."
  fi
}

clone_repo "$REPO_JAX"      "$JAX_DIR"      "JAX (las_manos/memoria)"
clone_repo "$REPO_PLATFORM" "$PLATFORM_DIR" "Axioma (jax-platform)"

if is_dry; then
  would "verificaría estructura: las_manos/server.py, jax/memory/worker.py, requirements.txt, backend/main.py, backend/requirements.txt, frontend/package.json"
else
  for p in "$JAX_DIR/las_manos/server.py" "$JAX_DIR/jax/memory/worker.py" \
           "$JAX_DIR/requirements.txt" "$PLATFORM_DIR/backend/main.py" \
           "$PLATFORM_DIR/backend/requirements.txt" "$PLATFORM_DIR/frontend/package.json"; do
    [ -f "$p" ] && ok "estructura: ${p#/home/fruiz/}" || warn "esperado pero ausente: $p"
  done
fi
checkpoint "Repos clonados. ¿Continuar con la restauración de la base de datos?"

# ───────────────────────────── FASE 2: Restaurar base de datos ─────────────────────────────
phase "2 · Restaurar base de datos MariaDB ($DB_NAME)"
[ -f "$DB_DUMP" ] || die "no está el dump: $DB_DUMP"

if is_dry; then
  TCOUNT="$(sudo "$MYSQL_CLI" -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DB_NAME'" 2>/dev/null || echo '?')"
  would "CREATE DATABASE IF NOT EXISTS $DB_NAME (utf8mb4)"
  if [ "${TCOUNT:-0}" = "0" ] || [ "${TCOUNT}" = "?" ]; then
    would "importar dump ($(du -h "$DB_DUMP" | cut -f1)) → $DB_NAME  (actualmente ${TCOUNT} tablas; ~33 esperadas)"
  else
    would "$DB_NAME YA tiene $TCOUNT tablas → PEDIRÍA confirmación antes de sobrescribir (el dump trae DROP TABLE)"
  fi
else
  sudo "$MYSQL_CLI" -e "CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci" \
    && ok "DB '$DB_NAME' existe/creada." || die "no se pudo crear la DB."

  TCOUNT="$(sudo "$MYSQL_CLI" -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DB_NAME'" 2>/dev/null || echo 0)"
  info "tablas actuales en '$DB_NAME': $TCOUNT"

  DO_IMPORT=1
  if [ "${TCOUNT:-0}" -gt 0 ]; then
    warn "La DB '$DB_NAME' YA tiene $TCOUNT tablas. Importar el dump las SOBRESCRIBIRÁ (el dump incluye DROP TABLE)."
    confirm "¿Sobrescribir '$DB_NAME' con el dump de migración?" || { DO_IMPORT=0; info "importación omitida por decisión del usuario."; }
  fi

  if [ "$DO_IMPORT" = "1" ]; then
    info "importando dump (esto puede tardar)…"
    if gunzip -c "$DB_DUMP" | sudo "$MYSQL_CLI" "$DB_NAME"; then
      NEWC="$(sudo "$MYSQL_CLI" -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DB_NAME'" 2>/dev/null || echo '?')"
      ok "dump importado. Tablas ahora: $NEWC (esperadas: 33)."
    else
      die "fallo importando el dump."
    fi
  fi
fi
checkpoint "Base de datos restaurada. ¿Continuar con el descifrado del .env?"

# ───────────────────────────── FASE 3: Descifrar .env ─────────────────────────────
phase "3 · Descifrar credenciales → $ENV_DEST"
[ -f "$ENV_ENC" ] || die "no está el .env cifrado: $ENV_ENC"

if is_dry; then
  would "install -d -m755 root:root /etc/jax"
  if [ -f "$ENV_DEST" ]; then would "$ENV_DEST YA existe → pediría confirmación antes de sobrescribir"; fi
  would "descifrar $ENV_ENC (openssl AES-256, -pass stdin) → temp umask 077 → install -m600 root:root $ENV_DEST"
  would "  (pediría la passphrase interactiva; hasta 3 intentos; shred+rm del temp al salir)"
  would "verificaría claves críticas (JAX_DB_*, JAX_JWT_SECRET) y avisaría si falta SMTP_*"
  would "opcional (confirmado): crear/actualizar usuario '$DB_NAME'.jax_user con GRANT, sourceando el .env como root"
else
  sudo install -d -m 755 -o root -g root /etc/jax && ok "/etc/jax listo."

  if [ -f "$ENV_DEST" ]; then
    warn "$ENV_DEST ya existe."
    if ! confirm "¿Sobrescribir $ENV_DEST con el descifrado del paquete?"; then
      info "conservando $ENV_DEST existente; salto el descifrado."
      SKIP_ENV=1
    fi
  fi

  if [ "${SKIP_ENV:-0}" != "1" ]; then
    { set +x; } 2>/dev/null                      # nunca trazar esta sección
    TMP_ENV="$(umask 077; mktemp /tmp/jaxenv.XXXXXX)"
    trap 'command -v shred >/dev/null && shred -u "$TMP_ENV" 2>/dev/null; rm -f "$TMP_ENV" 2>/dev/null' EXIT
    DECRYPTED=0
    for attempt in 1 2 3; do
      printf '%sPassphrase del .env cifrado (no se muestra):%s ' "$c_ylw" "$c_reset" > /dev/tty
      read -rs PASSPHRASE < /dev/tty; printf '\n' > /dev/tty
      if printf '%s' "$PASSPHRASE" \
           | openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$ENV_ENC" -pass stdin -out "$TMP_ENV" 2>/dev/null \
           && grep -q '=' "$TMP_ENV"; then
        DECRYPTED=1; PASSPHRASE=""; unset PASSPHRASE
        break
      fi
      PASSPHRASE=""; unset PASSPHRASE
      warn "passphrase incorrecta o dato corrupto (intento $attempt/3)."
    done
    [ "$DECRYPTED" = "1" ] || die "no se pudo descifrar el .env tras 3 intentos."

    sudo install -m 600 -o root -g root "$TMP_ENV" "$ENV_DEST" && ok "$ENV_DEST instalado (600 root:root)."
    MISSING=""
    for k in JAX_DB_HOST JAX_DB_NAME JAX_DB_USER JAX_DB_PASSWORD JAX_JWT_SECRET; do
      sudo grep -q "^$k=" "$ENV_DEST" || MISSING="$MISSING $k"
    done
    [ -z "$MISSING" ] && ok "claves críticas presentes en el .env." || warn "faltan claves en .env:$MISSING"
    sudo grep -q "^SMTP_" "$ENV_DEST" || warn "el .env NO tiene SMTP_* — el reset de contraseña no enviará email hasta agregarlas."
  fi

  # Paso OPCIONAL: recrear el grant del usuario de aplicación (el dump NO lo incluye).
  # Se ejecuta como root sourceando el .env (manejo de comillas idéntico al de la app);
  # el password nunca aparece en argv ni en logs — va por stdin a mariadb dentro del subshell.
  if confirm "¿Crear/actualizar el usuario MariaDB de la app (jax_user) con GRANT sobre $DB_NAME? (recomendado)"; then
    if sudo bash -s -- "$DB_NAME" "$MYSQL_CLI" <<'GRANTEOF'
      set -uo pipefail
      DB_NAME="$1"; CLI="$2"
      set -a; . /etc/jax/.env 2>/dev/null; set +a
      : "${JAX_DB_USER:?falta JAX_DB_USER en .env}"; : "${JAX_DB_PASSWORD:?falta JAX_DB_PASSWORD en .env}"
      HOST="${JAX_DB_HOST:-localhost}"; HOSTPART="localhost"
      { [ "$HOST" != "localhost" ] && [ "$HOST" != "127.0.0.1" ]; } && HOSTPART='%'
      "$CLI" <<SQL
CREATE USER IF NOT EXISTS '${JAX_DB_USER}'@'${HOSTPART}' IDENTIFIED BY '${JAX_DB_PASSWORD}';
ALTER USER '${JAX_DB_USER}'@'${HOSTPART}' IDENTIFIED BY '${JAX_DB_PASSWORD}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${JAX_DB_USER}'@'${HOSTPART}';
FLUSH PRIVILEGES;
SQL
GRANTEOF
    then ok "usuario de app con GRANT sobre '$DB_NAME' creado/actualizado."
    else warn "no se pudo crear el grant (¿faltan claves en .env?); revísalo a mano."
    fi
  fi
fi
checkpoint "Credenciales restauradas. ¿Continuar con los entornos Python?"

# ───────────────────────────── FASE 4: Entornos Python (venvs) ─────────────────────────────
phase "4 · Recrear entornos virtuales Python (limpios)"
info "Python del sistema: $(python3 --version 2>&1)"
python3 --version 2>&1 | grep -q "3.12" || warn "Se esperaba Python 3.12 (CLAUDE.md). Verifica compatibilidad de deps."

make_venv() {
  local label="$1" venv="$2" reqs="$3"
  if is_dry; then
    if [ ! -f "$reqs" ]; then would "$label: requirements ausente ($reqs) → omitiría (llega tras el clone)"; return 0; fi
    if [ -d "$venv" ]; then would "$label: .venv existe → preguntaría recrear vs. reinstalar; luego pip install -r $reqs"
    else would "$label: python3 -m venv $venv && pip install -r $reqs"; fi
    return 0
  fi
  [ -f "$reqs" ] || { warn "$label: no existe requirements ($reqs), omito."; return 0; }
  if [ -d "$venv" ]; then
    if confirm "$label: '.venv' ya existe. ¿Borrar y recrear desde cero?"; then
      rm -rf "$venv" && info "$label: venv anterior eliminado."
    else
      info "$label: conservo venv existente, solo reinstalo requirements."
    fi
  fi
  [ -d "$venv" ] || python3 -m venv "$venv" || { warn "$label: fallo creando venv"; return 1; }
  "$venv/bin/pip" install --upgrade pip >/dev/null 2>&1
  if "$venv/bin/pip" install -r "$reqs"; then ok "$label: deps instaladas ($(basename "$reqs"))."
  else warn "$label: fallo instalando requirements — revisa la salida."; fi
}

for entry in "${VENVS[@]}"; do
  IFS='|' read -r L V R <<< "$entry"
  make_venv "$L" "$V" "$R"
done
checkpoint "Venvs listos. ¿Continuar con el frontend (npm install)?"

# ───────────────────────────── FASE 5: Frontend (npm install) ─────────────────────────────
phase "5 · Frontend Axioma (npm install con Node $EXPECTED_NODE)"
FE="$PLATFORM_DIR/frontend"
if is_dry; then
  would "cd $FE && PATH=$NODE_BIN:\$PATH npm install   (verificaría node_modules/.bin/vite al terminar)"
elif [ -f "$FE/package.json" ]; then
  export PATH="$NODE_BIN:$PATH"
  info "node: $(node --version)  ·  npm: $(npm --version)"
  ( cd "$FE" && npm install ) && ok "npm install completado." || warn "npm install falló — revisa."
  [ -x "$FE/node_modules/.bin/vite" ] && ok "vite presente (node_modules/.bin/vite)." || warn "vite no encontrado tras npm install."
else
  warn "no existe $FE/package.json — omito frontend."
fi
checkpoint "Frontend listo. ¿Continuar con la descarga de modelos Ollama (~16 GB)?"

# ───────────────────────────── FASE 6: Modelos Ollama ─────────────────────────────
phase "6 · Modelos Ollama (pull idempotente)"
for m in "${OLLAMA_MODELS[@]}"; do
  if ollama list 2>/dev/null | awk '{print $1}' | grep -qE "^${m}(:|$)"; then
    ok "$m ya presente."
  elif is_dry; then
    would "ollama pull $m"
  else
    info "descargando $m …"
    ollama pull "$m" && ok "$m descargado." || warn "fallo descargando $m — reintenta manualmente."
  fi
done
checkpoint "Modelos listos. ¿Continuar con la instalación de units systemd (SIN arrancar)?"

# ───────────────────────────── FASE 7: systemd units (sin arrancar) ─────────────────────────────
phase "7 · Instalar units systemd (daemon-reload, SIN start ni enable)"
[ -d "$UNITS_DIR" ] || die "no está el directorio de units: $UNITS_DIR"

for u in "$UNITS_DIR"/*.service "$UNITS_DIR"/*.timer; do
  [ -e "$u" ] || continue
  base="$(basename "$u")"; dest="/etc/systemd/system/$base"
  if is_dry; then
    if [ -f "$dest" ]; then
      if sudo diff -q "$u" "$dest" >/dev/null 2>&1; then would "$base: idéntico al instalado → no cambiaría"
      else would "$base: difiere del instalado → pediría confirmación para sobrescribir"; fi
    else
      would "install -m644 root:root $u → $dest"
    fi
    continue
  fi
  if [ -f "$dest" ] && ! sudo diff -q "$u" "$dest" >/dev/null 2>&1; then
    warn "$base ya instalado y DIFIERE del paquete."
    confirm "¿Sobrescribir $base?" || { info "conservo el $base instalado."; continue; }
  fi
  sudo install -m 644 -o root -g root "$u" "$dest" && ok "instalado: $base"
done

if is_dry; then
  would "systemctl daemon-reload"
  would "NO haría enable ni start de ningún servicio (queda manual — ver checklist)"
else
  sudo systemctl daemon-reload && ok "systemctl daemon-reload hecho."
  info "Verificando rutas referidas por los units…"
  for u in "$UNITS_DIR"/*.service; do
    [ -e "$u" ] || continue
    while IFS= read -r path; do
      [ -e "$path" ] || warn "  $(basename "$u"): ruta aún ausente → $path"
    done < <(grep -hoE '(/home/fruiz|/etc/jax)[^ "]+' "$u" | sort -u)
  done
  warn "Los servicios NO se arrancaron ni habilitaron (por diseño). Ver checklist final."
fi

# ───────────────────────────── Checklist final ─────────────────────────────
if is_dry; then
  phase "✔ Fin del --dry-run (simulación)"
  ok "Simulación completada — no se tocó nada. Cuando estés conforme, re-ejecuta SIN --dry-run."
  exit 0
fi

phase "✔ Restauración completada — CHECKLIST de verificación manual"
cat <<'CHECKLIST'
Antes de arrancar los servicios, verifica MANUALMENTE:

 [ ] .env correcto:        sudo cat /etc/jax/.env        (revisa DB, JWT, API keys)
 [ ] SMTP configurado:     agregar SMTP_HOST/USER/PASSWORD/FROM al .env si vas a usar
                           el reset de contraseña (si no, solo loguea el link).
 [ ] FRONTEND_ORIGIN:      apuntando al dominio correcto en /etc/jax/.env
 [ ] DB conecta con app:   mariadb -u jax_user -p jax_memory -e "SHOW TABLES;"
                           (usa el user/pass del .env; deben verse ~33 tablas)
 [ ] Migración de schema:  la feature de auth necesita password_reset_tokens +
                           columnas failed_attempts/locked_until/last_login.
                           Si el dump ya las trae, ok; si no, corre las migraciones
                           del backend (backend/db/migrations.py).
 [ ] Modelos ollama:       ollama list   (deben estar los 4)
 [ ] GPU/ROCm:             confirmar que qwen3:14b corre en la R9700 (gfx1201),
                           no en CPU:  ollama ps   tras una consulta.
 [ ] Almacenamiento:       modelos/vector DB en /srv/jax-data si aplica
                           (OLLAMA_MODELS / rutas de datos).
 [ ] keyd (opcional):      es dependencia vendored; instálala aparte si la usas.

Arranque de servicios (uno por uno — VERIFICA cada 'start' ANTES de 'enable'):

    sudo systemctl start jax-las-manos        && systemctl status jax-las-manos
    sudo systemctl start jax-platform         && systemctl status jax-platform
    sudo systemctl start jax-platform-frontend && systemctl status jax-platform-frontend
    sudo systemctl start jax-memory-worker.timer

    # Solo cuando confirmes que cada uno arranca bien, habilítalos para el boot:
    sudo systemctl enable jax-las-manos jax-platform jax-platform-frontend jax-memory-worker.timer

Verificar salud tras arrancar:

    curl -s http://127.0.0.1:7777/            # LAS MANOS
    curl -s http://127.0.0.1:8080/api/health  # Axioma backend
    journalctl -u jax-platform -n 50 --no-pager

Deploy del frontend a producción (NO lo hace este script):
    Requiere build + rsync a la VM dev (ver /etc/jax/.env para el host).
    Ver "Lecciones operativas" en ~/jax-platform/CLAUDE.md.
CHECKLIST

ok "Fin. Revisa el checklist antes de arrancar servicios."
