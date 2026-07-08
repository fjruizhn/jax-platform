#!/usr/bin/env bash
#
# verify-hall9000.sh — Verificación de salud post-arranque del ecosistema JAX / Axioma
# -----------------------------------------------------------------------------------
# SOLO LECTURA: no arranca, no instala, no modifica nada. Seguro de re-ejecutar.
# Chequea: servicios systemd · puertos · 3 endpoints de salud · base de datos · ollama.
#
# Uso:
#   bash verify-hall9000.sh            # verificación completa (host 127.0.0.1)
#   HOST=otra.ip bash verify-hall9000.sh
#   bash verify-hall9000.sh --quiet    # solo el resumen y los fallos
#
# Exit code: 0 si no hay fallos críticos; 1 si hay ≥1 FAIL (útil para monitoreo/cron).
#
set -uo pipefail

HOST="${HOST:-127.0.0.1}"
QUIET=0
for a in "$@"; do case "$a" in
  --quiet|-q) QUIET=1 ;;
  -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
  *) printf 'Argumento desconocido: %s\n' "$a" >&2; exit 2 ;;
esac; done

# Puertos y endpoints (rutas confirmadas en el código fuente)
LASMANOS_URL="http://$HOST:7777/health"
BACKEND_URL="http://$HOST:8080/api/health"
FRONTEND_URL="http://$HOST:5173/"
SERVICES=(jax-las-manos jax-platform jax-platform-frontend)
TIMER=jax-memory-worker.timer
PORTS=(7777 8080 5173)
DB_NAME="jax_memory"
EXPECTED_TABLES=33
OLLAMA_MODELS=(nomic-embed-text qwen3:14b qwen2.5:7b llama3.2:3b)

# ───────────────────────────── Utilidades ─────────────────────────────
c_reset=$'\033[0m'; c_bold=$'\033[1m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_red=$'\033[31m'; c_blu=$'\033[36m'
P=0; W=0; F=0
pass() { P=$((P+1)); [ "$QUIET" = 1 ] || printf '%s✓ PASS%s %s\n' "$c_grn" "$c_reset" "$*"; }
soft() { W=$((W+1)); printf '%s⚠ WARN%s %s\n' "$c_ylw" "$c_reset" "$*"; }
hard() { F=$((F+1)); printf '%s✗ FAIL%s %s\n' "$c_red" "$c_reset" "$*" >&2; }
info() { [ "$QUIET" = 1 ] || printf '%s→%s %s\n' "$c_blu" "$c_reset" "$*"; }
section() { [ "$QUIET" = 1 ] || printf '\n%s── %s ──%s\n' "$c_bold" "$*" "$c_reset"; }
have() { command -v "$1" >/dev/null 2>&1; }

# HTTP: comprueba que la URL responda 2xx dentro del timeout.
check_http() {
  local label="$1" url="$2"
  local code; code="$(curl -sS -m 6 -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || echo 000)"
  case "$code" in
    2??) pass "$label → HTTP $code ($url)" ;;
    000) hard "$label → sin respuesta/timeout ($url)" ;;
    *)   hard "$label → HTTP $code ($url)" ;;
  esac
}

printf '%s╔══════════════════════════════════════════════════════════╗%s\n' "$c_bold" "$c_reset"
printf '%s║  verify-hall9000 · salud del ecosistema JAX/Axioma         ║%s\n' "$c_bold" "$c_reset"
printf '%s╚══════════════════════════════════════════════════════════╝%s\n' "$c_bold" "$c_reset"
info "host: $HOST   ·   $(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null || true)"

# ───────────────────────────── 1. Servicios systemd ─────────────────────────────
section "1 · Servicios systemd"
for s in "${SERVICES[@]}"; do
  act="$(systemctl is-active "$s" 2>/dev/null || true)"
  ena="$(systemctl is-enabled "$s" 2>/dev/null || true)"
  case "$act" in
    active)  pass "$s: active (enabled=$ena)" ;;
    failed)  hard "$s: FAILED  →  journalctl -u $s -n 40 --no-pager" ;;
    *)       hard "$s: $act (no está corriendo)" ;;
  esac
done
# memory-worker es oneshot: se valida por su TIMER + resultado de la última corrida
tact="$(systemctl is-active "$TIMER" 2>/dev/null || true)"
if [ "$tact" = "active" ]; then pass "$TIMER: active"; else soft "$TIMER: $tact (el worker no se disparará solo)"; fi
res="$(systemctl show jax-memory-worker.service -p Result --value 2>/dev/null || true)"
[ -n "$res" ] && info "última corrida jax-memory-worker.service: Result=$res"

# ───────────────────────────── 2. Puertos a la escucha ─────────────────────────────
section "2 · Puertos a la escucha"
if have ss; then LISTEN="$(ss -ltn 2>/dev/null)"; elif have netstat; then LISTEN="$(netstat -ltn 2>/dev/null)"; else LISTEN=""; soft "ni ss ni netstat disponibles — omito chequeo de puertos"; fi
if [ -n "$LISTEN" ]; then
  for p in "${PORTS[@]}"; do
    if printf '%s\n' "$LISTEN" | grep -qE "[:.]$p[[:space:]]"; then pass "puerto $p escuchando"; else hard "puerto $p NO está escuchando"; fi
  done
fi

# ───────────────────────────── 3. Endpoints de salud ─────────────────────────────
section "3 · Endpoints de salud (HTTP)"
if have curl; then
  check_http "LAS MANOS (7777)"      "$LASMANOS_URL"
  check_http "Axioma backend (8080)" "$BACKEND_URL"
  check_http "Frontend Vite (5173)"  "$FRONTEND_URL"
else
  hard "curl no está instalado — no puedo verificar endpoints"
fi

# ───────────────────────────── 4. Base de datos ─────────────────────────────
section "4 · Base de datos MariaDB ($DB_NAME)"
MYSQL_CLI="mariadb"; have mariadb || MYSQL_CLI="mysql"
if have "$MYSQL_CLI"; then
  # 4a. Acceso administrativo por socket (cuenta el schema)
  TCOUNT="$(sudo -n "$MYSQL_CLI" -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DB_NAME'" 2>/dev/null || echo '')"
  if [ -z "$TCOUNT" ]; then
    soft "no pude consultar por socket (sudo). Corre con sudo disponible para el chequeo completo de DB."
  elif [ "$TCOUNT" -ge "$EXPECTED_TABLES" ]; then
    pass "DB '$DB_NAME' con $TCOUNT tablas (esperadas ≥$EXPECTED_TABLES)"
  elif [ "$TCOUNT" -gt 0 ]; then
    soft "DB '$DB_NAME' tiene $TCOUNT tablas (<$EXPECTED_TABLES esperadas) — ¿import incompleto?"
  else
    hard "DB '$DB_NAME' sin tablas — restauración no aplicada"
  fi

  # 4b. El usuario de aplicación (jax_user) conecta con las credenciales del .env
  if [ -r /etc/jax/.env ] || sudo -n test -r /etc/jax/.env 2>/dev/null; then
    if sudo -n bash -s -- "$DB_NAME" "$MYSQL_CLI" 2>/dev/null <<'DBEOF'
      set -uo pipefail
      DB_NAME="$1"; CLI="$2"
      set -a; . /etc/jax/.env 2>/dev/null; set +a
      [ -n "${JAX_DB_USER:-}" ] && [ -n "${JAX_DB_PASSWORD:-}" ] || exit 3
      export MYSQL_PWD="$JAX_DB_PASSWORD"
      "$CLI" -u"$JAX_DB_USER" -h"${JAX_DB_HOST:-localhost}" "$DB_NAME" -e "SELECT 1" >/dev/null 2>&1
DBEOF
    then pass "usuario de app conecta a '$DB_NAME' (GRANT correcto)"
    else soft "el usuario de app NO conecta a '$DB_NAME' (revisa GRANT / password del .env)"
    fi
  else
    info "/etc/jax/.env no legible sin sudo — omito prueba de login de la app"
  fi
else
  hard "cliente MariaDB no instalado"
fi

# ───────────────────────────── 5. Ollama / GPU ─────────────────────────────
section "5 · Ollama (modelos + GPU)"
if have ollama && ollama list >/dev/null 2>&1; then
  LIST="$(ollama list 2>/dev/null)"
  for m in "${OLLAMA_MODELS[@]}"; do
    if printf '%s\n' "$LIST" | awk '{print $1}' | grep -qE "^${m}(:|$)"; then pass "modelo presente: $m"; else hard "modelo ausente: $m"; fi
  done
  # Modelos cargados ahora y en qué procesador (GPU esperado: R9700/gfx1201)
  PS="$(ollama ps 2>/dev/null)"
  if printf '%s\n' "$PS" | grep -q '[0-9]'; then
    if printf '%s\n' "$PS" | grep -qiE '100% *CPU|[0-9]+% *CPU'; then
      soft "hay modelo(s) corriendo en CPU (¿ROCm no activo?):"; printf '%s\n' "$PS" | sed 's/^/    /'
    else
      pass "modelos cargados corren en GPU:"; [ "$QUIET" = 1 ] || printf '%s\n' "$PS" | sed 's/^/    /'
    fi
  else
    info "ningún modelo cargado ahora mismo (normal sin tráfico). Para verificar GPU: haz una consulta y re-ejecuta ('ollama ps')."
  fi
else
  hard "ollama no responde"
fi

# ───────────────────────────── Resumen ─────────────────────────────
printf '\n%s══════════════════════════════════════════════════════════%s\n' "$c_bold" "$c_reset"
printf '%s  RESUMEN%s   %s✓ %d PASS%s   %s⚠ %d WARN%s   %s✗ %d FAIL%s\n' \
  "$c_bold" "$c_reset" "$c_grn" "$P" "$c_reset" "$c_ylw" "$W" "$c_reset" "$c_red" "$F" "$c_reset"
printf '%s══════════════════════════════════════════════════════════%s\n' "$c_bold" "$c_reset"

if [ "$F" -gt 0 ]; then
  err_hint() { printf '%s→%s %s\n' "$c_red" "$c_reset" "$*"; }
  err_hint "Hay $F fallo(s) crítico(s). Diagnóstico rápido:"
  err_hint "  systemctl status ${SERVICES[*]}"
  err_hint "  journalctl -u jax-platform -u jax-las-manos -n 60 --no-pager"
  exit 1
fi
[ "$W" -gt 0 ] && printf '%sTodo lo crítico OK, con %d aviso(s) a revisar.%s\n' "$c_ylw" "$W" "$c_reset"
[ "$W" -eq 0 ] && printf '%sTodo verde. Ecosistema operativo.%s\n' "$c_grn" "$c_reset"
exit 0
