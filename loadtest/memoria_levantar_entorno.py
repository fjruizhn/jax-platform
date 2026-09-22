"""Levanta el backend (127.0.0.1:18080) de este worktree contra la base
sembrada por memoria_seed.py, PERSISTENTE -- no lo mata al salir (Task 7,
Parte D del encargo: Fernando quiere mirar la pantalla en un navegador).

Mismo metodo de aislamiento que loadtest/historial_orquestar.py (un solo
proceso uvicorn real, HTTP de punta a punta, nunca ASGITransport), pero:
  - la base es `BASE_DE_PRUEBA` (propia, clonada de jax_memory_test, NUNCA
    jax_memory ni jax_memory_test), pasada por argumento;
  - los servicios que el lifespan sondea (LAS MANOS, Jacobs, la propia
    plataforma) quedan apuntando a un puerto donde no escucha nada --
    aislados de PRODUCCION (:7777/:8080), y esta pantalla no los necesita;
  - JAX_OLLAMA_URL SI apunta al Ollama real (localhost:11434, el mismo que
    usa produccion): si Fernando corrige un hecho desde la pantalla, el
    embedding se calcula de verdad. Es una llamada de lectura al servicio de
    inferencia, no una escritura a la base de produccion.
  - el proceso se lanza con start_new_session=True y este script SALE
    inmediatamente despues de confirmar el arranque: no lo espera, no lo
    mata. Sobrevive a que termine la sesion que lo lanzo.

USO:
    python3 loadtest/memoria_levantar_entorno.py <base_de_prueba> <password_superadmin>

Imprime, al final, JSON con pid, url, email y la contraseña usada.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

LOADTEST_DIR = Path(__file__).parent
BACKEND_DIR = LOADTEST_DIR.parent / "backend"
RUN_DIR = LOADTEST_DIR / "_run"

BACKEND_PORT = 18080
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"
PUERTOS_DE_PRODUCCION = {7777, 8080}

SUPERADMIN_EMAIL = "superadmin-memoria-carga@example.invalid"
TENANT_NAME = "Tenant de la carga de Memoria (Task 7)"

# JAX_REPO_PATH, PROHIBIDO tocar /srv/jax-prod/jax o /home/fruiz/jax (el
# primero es producción; el segundo es el checkout de trabajo de OTRAS
# sesiones -- usarlo como JAX_REPO_PATH de una medición no lo modifica, pero
# la mezcla de "qué versión de `jax` corrió esta medición" con "qué hay en el
# checkout de otra sesión ahora mismo" es exactamente la ambigüedad que la
# reproducibilidad no puede tener).
#
# CORRECCIÓN (revisión adversarial de jax-platform PR 146, tercera vuelta,
# M5): esta constante apuntaba antes a `/home/fruiz/worktrees/jax-memoria`,
# un worktree compañero que ya no existe (se limpia entre rondas) -- CUALQUIERA
# que corriera este script tal cual fallaba al arrancar el backend, y una
# medición de carga corrida contra ESE hallazgo terminó usando
# `/home/fruiz/jax` a mano, que este mismo comentario prohíbe. La solución no
# es otra ruta fija (que se pudre exactamente igual) -- es un checkout PROPIO,
# clonado por este script, así la medición se puede repetir sin preparar nada
# a mano y sin tocar ningún checkout ajeno. Ver `_asegurar_checkout_de_jax`.
JAX_REPO_GIT_URL = "https://github.com/fjruizhn/Jax.git"


def _cargar_env_produccion() -> dict:
    r = subprocess.run(["sudo", "-n", "cat", "/etc/jax/.env"], capture_output=True, text=True, check=True)
    env = {}
    for linea in r.stdout.splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            env[k.strip()] = v.strip()
    return env


def _asegurar_checkout_de_jax(tmp: Path) -> Path:
    """Checkout PROPIO de `jax` para `JAX_REPO_PATH` -- nunca `/home/fruiz/jax`
    ni `/srv/jax-prod/jax` (prohibido, ver el comentario de
    `JAX_REPO_GIT_URL`). Se clona UNA vez dentro del propio `RUN_DIR` de
    este script (idempotente: si ya existe, no vuelve a clonar) -- mismo
    remoto que ya usa `.github/workflows/policy.yml` para el mismo fin
    (`git clone --depth=1 .../Jax.git`). Así la medición se puede repetir
    sin preparar ningún worktree compañero a mano."""
    destino = tmp / "jax-repo"
    if not (destino / "jax" / "memory" / "db.py").exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth=1", JAX_REPO_GIT_URL, str(destino)],
            check=True,
        )
    return destino


def construir_env(base_de_prueba: str, password_superadmin: str, tmp: Path) -> dict:
    env = dict(os.environ)
    env.update(_cargar_env_produccion())
    env["JAX_DB_NAME"] = base_de_prueba
    env["JAX_REPO_PATH"] = str(_asegurar_checkout_de_jax(tmp))
    env["LAS_MANOS_URL"] = "http://127.0.0.1:9"
    env["JACOBS_URL"] = "http://127.0.0.1:9/jacobs"
    env["JAX_PLATFORM_URL"] = BACKEND_URL
    env["CANARY_INTERVAL_SECONDS"] = "0"
    env["JAX_LAS_MANOS_CREDENCIAL_PLATAFORMA"] = secrets.token_urlsafe(40)
    env["FERNET_KEY"] = base64.urlsafe_b64encode(os.urandom(32)).decode()

    env["JAX_USAGE_SPOOL_DIR"] = str(tmp / "usage-spool")
    env["JAX_FACET_SEAL_PATH"] = str(tmp / "facet-cache-seal")
    env["JAX_KILL_SWITCH_PATH"] = str(tmp / "interruptor" / "PAUSE")
    env["JAX_EJECUTOR_PAUSA"] = str(tmp / "ejecutor-pausa" / "PAUSA")
    env["JAX_EJECUTOR_PYTHON"] = str(tmp / "no-existe" / "python")
    env["JAX_MISSIONS_DIR"] = str(tmp / "missions")
    env["JAX_REPO_BASE"] = str(tmp / "repo")
    env["JAX_AUDIT_LOG_PATH"] = str(tmp / "audit.jsonl")
    env["JAX_BIN"] = str(tmp / "bin" / "jax")
    env["JAX_ADJUNTOS_DIR"] = str(tmp / "adjuntos")
    env["JAX_PROXY_CARRIL_RAIZ"] = str(tmp / "carril")
    for k, v in {
        "JAX_ADJUNTO_MAX_BYTES": "10485760", "JAX_ADJUNTO_MAX_CHARS": "8000",
        "JAX_ADJUNTO_MAX_PAGINAS": "20", "JAX_ADJUNTO_MAX_POR_MENSAJE": "1",
        "JAX_ADJUNTO_IMAGENES_EN_PROCESO": "1", "JAX_ADJUNTO_SUBIDAS_EN_PROCESO": "1",
        "JAX_ADJUNTO_PDF_PROCESOS": "1", "JAX_ADJUNTO_PDF_TIMEOUT_SEGUNDOS": "5",
        "JAX_ADJUNTOS_TTL_HORAS": "24", "JAX_ADJUNTOS_CUOTA_BYTES_USUARIO": "524288000",
        "JAX_ADJUNTOS_DISCO_LIBRE_MINIMO_BYTES": "1073741824",
        "JAX_ADJUNTOS_SUBIDAS_POR_MINUTO": "600", "JAX_ADJUNTOS_RECHAZO_ESPERA_MS": "0",
        "JAX_SEED_SUPERADMIN_EMAIL": SUPERADMIN_EMAIL,
        "JAX_SEED_TENANT_NAME": TENANT_NAME,
        "JAX_SEED_ADMIN_PASSWORD": password_superadmin,
    }.items():
        env[k] = v
    env["PYTHONPATH"] = str(BACKEND_DIR)
    return env


def _verificar_no_apunta_a_produccion(env: dict, base_de_prueba: str) -> None:
    if BACKEND_PORT in PUERTOS_DE_PRODUCCION:
        raise RuntimeError(f"BACKEND_PORT={BACKEND_PORT} pisa produccion -- ABORTANDO.")
    if env.get("JAX_DB_NAME") != base_de_prueba or base_de_prueba in ("jax_memory", "jax_memory_test"):
        raise RuntimeError(f"JAX_DB_NAME={env.get('JAX_DB_NAME')!r} invalido -- ABORTANDO.")
    for var in ("LAS_MANOS_URL", "JACOBS_URL"):
        puerto = urlparse(env.get(var, "")).port
        if puerto in PUERTOS_DE_PRODUCCION:
            raise RuntimeError(f"{var}={env.get(var)!r} pisa produccion -- ABORTANDO.")


def esperar_puerto(host: str, port: int, timeout: float = 40.0) -> None:
    import socket
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.3)
    raise RuntimeError(f"{host}:{port} no respondio en {timeout}s")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("uso: memoria_levantar_entorno.py <base_de_prueba> <password_superadmin>")
    base_de_prueba, password_superadmin = sys.argv[1], sys.argv[2]

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    env = construir_env(base_de_prueba, password_superadmin, RUN_DIR)
    _verificar_no_apunta_a_produccion(env, base_de_prueba)

    log_backend = open(RUN_DIR / "backend.log", "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
         "--port", str(BACKEND_PORT), "--log-level", "info"],
        cwd=str(BACKEND_DIR), env=env, stdout=log_backend, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    (RUN_DIR / "backend.pid").write_text(str(proc.pid))

    esperar_puerto("127.0.0.1", BACKEND_PORT, timeout=60.0)
    t0 = time.time()
    r = None
    while time.time() - t0 < 30:
        try:
            r = httpx.get(f"{BACKEND_URL}/api/health", timeout=3.0)
            if r.status_code < 500:
                break
        except Exception:  # fail-soft: el backend todavia esta arrancando, la conexion rechazada es esperable dentro de la ventana de 30s -- se reintenta hasta el timeout, no hay nada que loguear en cada intento
            pass
        time.sleep(0.5)

    environ = Path(f"/proc/{proc.pid}/environ").read_bytes()
    pares = dict(p.split(b"=", 1) for p in environ.split(b"\x00") if b"=" in p)
    db_name = pares.get(b"JAX_DB_NAME", b"").decode()
    if db_name != base_de_prueba:
        raise RuntimeError(f"proceso real con JAX_DB_NAME={db_name!r} -- pero sigue vivo, revisar a mano")

    resultado = {
        "pid": proc.pid,
        "url": BACKEND_URL,
        "health_status": r.status_code if r else None,
        "db_name_verificado": db_name,
        "superadmin_email": SUPERADMIN_EMAIL,
        "superadmin_password": password_superadmin,
        "log": str(RUN_DIR / "backend.log"),
    }
    (RUN_DIR / "info.json").write_text(json.dumps(resultado, indent=2))
    print(json.dumps(resultado, indent=2))


if __name__ == "__main__":
    main()
