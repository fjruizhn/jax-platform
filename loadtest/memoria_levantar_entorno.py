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


def _asegurar_checkout_de_jax(tmp: Path, commit: str | None = None) -> tuple[Path, str]:
    """Checkout PROPIO de `jax` para `JAX_REPO_PATH` -- nunca `/home/fruiz/jax`
    ni `/srv/jax-prod/jax` (prohibido, ver el comentario de
    `JAX_REPO_GIT_URL`).

    MINOR 4 (revisión adversarial de jax-platform PR 146, ronda 4): la
    versión anterior clonaba `--depth=1` UNA sola vez y no lo volvía a
    tocar -- una corrida de carga podía terminar usando un `jax` de hace
    semanas sin que nadie lo notara, y el commit usado ni siquiera quedaba
    registrado en `info.json`. Acá: el clon es COMPLETO (sin `--depth`, para
    poder moverse a cualquier commit que se pida, no sólo al HEAD del
    momento del clon) y se reusa entre corridas (`RUN_DIR` es el mismo), pero
    en CADA corrida se hace `fetch` + `checkout --force` al commit pedido
    (o a `origin/master` si no se pidió ninguno) -- nunca queda un checkout
    viejo sirviendo una medición nueva. Devuelve la ruta Y el commit
    resuelto (`git rev-parse HEAD`), para que el llamador lo deje escrito en
    `info.json` -- antes ese dato se perdía.

    SEGURIDAD (ronda 5): `commit`, si se pide, tiene que ser ANCESTRO de
    `origin/master` -- ver el porqué junto al chequeo, más abajo."""
    destino = tmp / "jax-repo"
    if not (destino / ".git").exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", JAX_REPO_GIT_URL, str(destino)], check=True)
    subprocess.run(["git", "-C", str(destino), "fetch", "--all", "--prune", "--tags"], check=True)
    ref = commit or "origin/master"
    # SEGURIDAD (revision adversarial de jax-platform PR 146, ronda 5): este
    # lanzador copia credenciales de PRODUCCION al entorno del backend que
    # arranca (`_cargar_env_produccion`) -- sin este chequeo, un
    # `commit_de_jax` arbitrario (cualquier rama, cualquier fork con acceso
    # de push, cualquier commit sin revisar) corria CON esas credenciales.
    # `origin/master` ya esta al dia (el `fetch` de arriba corre siempre
    # antes). Sin `commit` explicito no hay nada que validar: `ref` ya es
    # `origin/master`.
    if commit:
        ancestro = subprocess.run(
            ["git", "-C", str(destino), "merge-base", "--is-ancestor", commit, "origin/master"],
        )
        if ancestro.returncode != 0:
            raise RuntimeError(
                f"commit_de_jax={commit!r} no es ancestro de origin/master -- "
                "este lanzador copia credenciales de PRODUCCION al entorno "
                "que arranca; no corre un commit arbitrario sin pasar por "
                "revision.")
    subprocess.run(["git", "-C", str(destino), "checkout", "--force", "--detach", ref], check=True)
    resuelto = subprocess.run(
        ["git", "-C", str(destino), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return destino, resuelto


def construir_env(base_de_prueba: str, password_superadmin: str, tmp: Path,
                   commit_de_jax: str | None = None) -> tuple[dict, str]:
    env = dict(os.environ)
    env.update(_cargar_env_produccion())
    env["JAX_DB_NAME"] = base_de_prueba
    # SEGURIDAD (revision adversarial de jax-platform PR 146, ronda 5):
    # `_cargar_env_produccion()` copia TODO `/etc/jax/.env`, incluido
    # `JAX_JWT_SECRET` -- sin esta linea, el backend de carga firmaba (y
    # verificaba) tokens con la MISMA llave que produccion, y cualquier
    # script de medicion (memoria_medir.py) podia fabricar un token de
    # superadmin valido tanto para el backend de carga COMO para
    # produccion. El entorno de carga genera su PROPIA llave, aleatoria,
    # nueva en cada corrida -- los scripts de medicion la leen del entorno
    # del proceso YA LEVANTADO (`/proc/<pid>/environ`, ver `main()` mas
    # abajo), nunca de `/etc/jax/.env`.
    env["JAX_JWT_SECRET"] = secrets.token_urlsafe(48)
    ruta_jax, jax_commit_resuelto = _asegurar_checkout_de_jax(tmp, commit_de_jax)
    env["JAX_REPO_PATH"] = str(ruta_jax)
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
    return env, jax_commit_resuelto


def _verificar_no_apunta_a_produccion(env: dict, base_de_prueba: str) -> None:
    if BACKEND_PORT in PUERTOS_DE_PRODUCCION:
        raise RuntimeError(f"BACKEND_PORT={BACKEND_PORT} pisa produccion -- ABORTANDO.")
    if env.get("JAX_DB_NAME") != base_de_prueba or base_de_prueba in ("jax_memory", "jax_memory_test"):
        raise RuntimeError(f"JAX_DB_NAME={env.get('JAX_DB_NAME')!r} invalido -- ABORTANDO.")
    for var in ("LAS_MANOS_URL", "JACOBS_URL"):
        puerto = urlparse(env.get(var, "")).port
        if puerto in PUERTOS_DE_PRODUCCION:
            raise RuntimeError(f"{var}={env.get(var)!r} pisa produccion -- ABORTANDO.")


def leer_environ_de_proceso(pid: int) -> dict[str, str]:
    """`/proc/<pid>/environ` del proceso YA LEVANTADO -- la unica fuente
    confiable de "con que variables corre de verdad" (un `env` en memoria
    de este script podria divergir si algo lo reescribe entre construirlo y
    lanzar el proceso). La usa `main()` para verificar `JAX_DB_NAME`, y
    scripts de medicion (memoria_medir.py) para leer el `JAX_JWT_SECRET` de
    CARGA -- el que de verdad FIRMA los tokens de la medicion -- que sale
    de ACA, nunca de `/etc/jax/.env` (SEGURIDAD, ronda 5: esa es la llave
    de produccion; el entorno de carga tiene la suya propia, generada en
    `construir_env`).

    m6/m4 (cierre jax-platform#146, texto corregido en la ronda 7): esto NO
    significa que los scripts de medicion dejen de leer `/etc/jax/.env` --
    lo leen entero (`sudo -n cat`), `JAX_JWT_SECRET` de produccion
    incluido, y ese valor SI se usa, pero sólo para COMPARARLO (`!=`)
    contra el secreto de carga que devuelve esta función -- nunca para
    firmar, nunca para imprimir. Es la barrera de seguridad de ronda 5: si
    algún día coincidieran, el script aborta en vez de medir."""
    environ = Path(f"/proc/{pid}/environ").read_bytes()
    pares = dict(p.split(b"=", 1) for p in environ.split(b"\x00") if b"=" in p)
    return {k.decode(): v.decode() for k, v in pares.items()}


def abortar_si_el_secreto_de_carga_coincide_con_produccion(
        jwt_secret_de_carga: str, jwt_secret_de_produccion: str | None) -> None:
    """Barrera de seguridad compartida entre `memoria_medir.py` y
    `memoria_medir_fundir.py` -- función pura (sin I/O) para poder
    testearla aislada, ver `test_memoria_levantar_entorno.py`. Nunca
    imprime ninguno de los dos argumentos (ni acá ni en el `raise`).

    MINOR (revisión adversarial, ronda 8 -- consistencia): la versión
    anterior de este chequeo, duplicada inline en los dos scripts, sólo
    comparaba `jwt_secret_de_carga == jwt_secret_de_produccion` -- un
    secreto de CARGA vacío (`environ_de_carga.get("JAX_JWT_SECRET")`
    devolviendo `""`, p. ej. si `construir_env()` nunca llegó a poner la
    variable) NO coincide con un secreto de producción no vacío, así que
    el chequeo lo dejaba pasar: medir con un backend que no firma nada
    válido (o que cae a algún default silencioso) no es mejor que medir
    contra producción -- es otra forma de medir sobre un entorno que no
    es el que se cree que es. `historial_orquestar.py` (punto 7, ronda 7)
    ya tenía este chequeo completo (`not jwt_secret_de_carga or ...`);
    esta función lo unifica para los otros dos scripts.

    `assert` desaparece con `python -O` (PYTHONOPTIMIZE=1): esta barrera
    es de seguridad, no un chequeo de desarrollo, así que aborta con
    `SystemExit` en vez de depender de que nadie corra estos scripts
    optimizados."""
    if not jwt_secret_de_carga or jwt_secret_de_carga == jwt_secret_de_produccion:
        raise SystemExit(
            "el backend de carga esta firmando con la llave de PRODUCCION (o "
            "sin ninguna) -- ABORTANDO, no se mide sobre un entorno que puede "
            "emitir tokens validos tambien contra produccion")


def escribir_info_json(info_path: Path, resultado: dict) -> None:
    """Escribe `info_path` (JSON) SIEMPRE en modo 600, sin ventana y sin
    importar si el archivo ya existía con otro modo. Función pura de I/O,
    separada de `main()` para poder testearla aislada con un archivo
    temporal -- ver `test_memoria_levantar_entorno.py`.

    MINOR 4 (ronda 4): `info.json` trae `superadmin_password` en texto
    plano (de la base de CARGA, nunca de producción -- pero igual es un
    secreto utilizable) -- 600, sólo el dueño puede leerlo.

    SEGURIDAD (ronda 5): `write_text()` + `chmod()` por separado deja una
    VENTANA real -- el archivo nace con el umask de la sesión (típicamente
    644, ya con el contenido completo escrito) y sólo DESPUÉS pasa a 600;
    cualquier lector entre esas dos líneas ve la contraseña. `os.open` con
    el modo 600 puesto en la LLAMADA que crea el archivo (`O_CREAT`) no
    tiene esa ventana -- el archivo nunca existe con otro modo.

    m7 (cierre, ronda 6): el `mode` de `os.open()` sólo aplica cuando el
    archivo se CREA -- si `info_path` ya existía de una corrida anterior
    (con OTRO modo, p. ej. 664 por el umask de esa sesión), `O_CREAT` sobre
    un archivo existente lo IGNORA por completo (POSIX open(2)): el archivo
    queda truncado y reescrito, pero con el modo viejo. Reproducido: un
    `info.json` previo en 664 seguía en 664 después de esta llamada.

    MINOR (revisión adversarial, ronda 7): el arreglo de m7 usaba
    `os.fchmod(fd, 0o600)` sobre el descriptor YA abierto -- correcto para
    el MODO del archivo, pero `fchmod` no le quita el descriptor a nadie
    que YA lo tuviera abierto. Si otro proceso abrió `info_path` ANTES de
    esta llamada (mientras el archivo todavía tenía el modo viejo, p. ej.
    664), ese descriptor sigue siendo válido -- los permisos de Unix se
    chequean al ABRIR, no en cada lectura -- y como `O_TRUNC` reusa el
    MISMO inodo, ese lector viejo sigue viendo (ahora) el contenido NUEVO
    (con la contraseña), sin que el `fchmod` lo afecte para nada: el
    `fchmod` cambia el modo del inodo, no revoca descriptores ajenos.

    La solución no es otro `chmod` -- es que el archivo NUEVO sea un INODO
    NUEVO: `os.unlink()` (si existía) desconecta el nombre del inodo viejo
    -- cualquier lector que ya lo tuviera abierto se queda con ESE inodo
    (huérfano, sin más escrituras), nunca ve el contenido nuevo -- y
    `O_CREAT | O_EXCL` crea un inodo NUEVO con el modo 600 puesto desde el
    primer instante en que existe, sin ventana. `O_EXCL` además es una
    barrera honesta: si alguien vuelve a crear el archivo entre el
    `unlink` y el `open` (carrera real, no esperada en este uso de un solo
    lanzador), esta llamada FALLA en vez de escribir sobre un archivo
    ajeno en silencio."""
    datos = json.dumps(resultado, indent=2).encode()
    try:
        os.unlink(info_path)
    except FileNotFoundError:  # fail-soft: primera corrida, info.json todavia no existe -- nada que desconectar
        pass
    fd = os.open(info_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, datos)
    finally:
        os.close(fd)


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
    if len(sys.argv) not in (3, 4):
        raise SystemExit(
            "uso: memoria_levantar_entorno.py <base_de_prueba> <password_superadmin> "
            "[commit_de_jax]"
        )
    base_de_prueba, password_superadmin = sys.argv[1], sys.argv[2]
    # MINOR 4 (ronda 4): commit opcional -- sin él, se usa `origin/master`
    # (siempre al día, ver `_asegurar_checkout_de_jax`); con él, se puede
    # reproducir una medición exacta contra un commit de `jax` específico.
    commit_de_jax = sys.argv[3] if len(sys.argv) == 4 else None

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    env, jax_commit_resuelto = construir_env(base_de_prueba, password_superadmin, RUN_DIR, commit_de_jax)
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

    environ = leer_environ_de_proceso(proc.pid)
    db_name = environ.get("JAX_DB_NAME", "")
    if db_name != base_de_prueba:
        raise RuntimeError(f"proceso real con JAX_DB_NAME={db_name!r} -- pero sigue vivo, revisar a mano")

    resultado = {
        "pid": proc.pid,
        "url": BACKEND_URL,
        "health_status": r.status_code if r else None,
        "db_name_verificado": db_name,
        # MINOR 4 (ronda 4): antes este dato se perdía -- una medición vieja
        # no podía decir de qué `jax` salió. Ver `_asegurar_checkout_de_jax`.
        "jax_commit": jax_commit_resuelto,
        "superadmin_email": SUPERADMIN_EMAIL,
        "superadmin_password": password_superadmin,
        "log": str(RUN_DIR / "backend.log"),
    }
    info_path = RUN_DIR / "info.json"
    # Ver `escribir_info_json` -- siempre 600, sin ventana, sin depender de
    # si `info_path` ya existía de una corrida anterior (m7, cierre ronda 6).
    escribir_info_json(info_path, resultado)
    print(json.dumps(resultado, indent=2))


if __name__ == "__main__":
    main()
