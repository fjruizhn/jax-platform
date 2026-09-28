"""ops/desplegar-sin-ventana-de-sync.sh -- despliega un cambio que toca el
nombre del candado o el ejecutor programado del catálogo de modelos, sin la
ventana de dos syncs concurrentes (2026-09-27, séptima ronda de la auditoría
adversarial).

SIEMPRE contra herramientas SIMULADAS -- `systemctl`, `mariadb`, `git`,
`curl`, `install`, `systemd-analyze` y `sudo` son ejecutables FALSOS, propios
de este archivo, puestos primero en el PATH del subproceso que corre el
guion real. Nunca se ejecuta contra producción ni contra un `systemd`/
`mariadb` reales -- ninguna de estas pruebas necesita `sudo` de verdad (el
guion se corre con `bash` a secas, no con `sudo bash`: todo lo que el guion
real necesitaría privilegios para hacer está detrás de un ejecutable falso
en el PATH, que no los necesita).

Cada ejecutable falso deja registrada su invocación (nombre + argumentos) en
`$FAKE_LOG` -- así los tests pueden afirmar cosas como "nunca se llegó a
reiniciar jax-platform" mirando el log, no adivinando por el código de
salida solo.
"""
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "ops" / "desplegar-sin-ventana-de-sync.sh"

SHA_ESPERADO = "a" * 40


def _escribir_ejecutable(ruta: Path, contenido: str) -> None:
    ruta.write_text(contenido)
    ruta.chmod(ruta.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _armar_bin_falso(bin_dir: Path) -> None:
    """Arma los ejecutables falsos. Todos loguean su invocación en
    `$FAKE_LOG` (una línea `nombre arg1 arg2 ...`) antes de decidir qué
    responder -- eso es lo que permite comprobar, por ejemplo, que un fallo
    en el freno autoritativo (paso 3) nunca llega a reiniciar jax-platform
    (paso 5): si "systemctl restart jax-platform" nunca aparece en el log,
    no se llegó."""
    _escribir_ejecutable(bin_dir / "systemctl", r'''#!/usr/bin/env bash
echo "systemctl $*" >> "$FAKE_LOG"
case "$1" in
  stop)
    exit "${FAKE_STOP_TIMER_CODIGO:-0}"
    ;;
  show)
    # show -p ActiveState --value <unidad>
    echo "${FAKE_ACTIVESTATE:-inactive}"
    exit 0
    ;;
  list-jobs)
    if [ "${FAKE_JOBS_PENDIENTES:-0}" = "1" ]; then
      echo "1 jax-catalogo-modelos.service/start waiting"
    fi
    exit 0
    ;;
  restart)
    exit "${FAKE_RESTART_JAX_PLATFORM_CODIGO:-0}"
    ;;
  daemon-reload)
    exit "${FAKE_DAEMON_RELOAD_CODIGO:-0}"
    ;;
  start)
    exit "${FAKE_START_TIMER_CODIGO:-0}"
    ;;
  list-timers)
    echo "NEXT                        LEFT UNIT"
    echo "(fake) proxima corrida      1h   jax-catalogo-modelos.timer"
    exit 0
    ;;
  *)
    echo "systemctl-falso: subcomando no soportado: $1" >&2
    exit 99
    ;;
esac
''')

    _escribir_ejecutable(bin_dir / "mariadb", r'''#!/usr/bin/env bash
echo "mariadb $*" >> "$FAKE_LOG"
sql=""
for arg in "$@"; do
  if [ -n "$sql" ]; then
    sql="$arg"
    break
  fi
  if [ "$arg" = "-e" ]; then
    sql="__siguiente__"
  fi
done
if [ "${FAKE_MARIADB_FALLA:-0}" = "1" ]; then
  echo "ERROR 2002 (HY000): fake -- conexion rechazada" >&2
  exit 1
fi
case "$sql" in
  *IS_USED_LOCK*)
    printf '%s\t%s\n' "${FAKE_CANDADO_VIEJO:-NULL}" "${FAKE_CANDADO_NUEVO:-NULL}"
    ;;
  *information_schema.TABLES*)
    echo "${FAKE_TABLA_EXISTE:-0}"
    ;;
  *catalogo_sync_ejecucion*)
    echo "${FAKE_FILAS_CORRIENDO:-0}"
    ;;
  *)
    echo "mariadb-falso: SQL no reconocido: $sql" >&2
    exit 98
    ;;
esac
''')

    _escribir_ejecutable(bin_dir / "git", r'''#!/usr/bin/env bash
echo "git $*" >> "$FAKE_LOG"
case "$*" in
  *"fetch origin"*)
    exit "${FAKE_GIT_FETCH_CODIGO:-0}"
    ;;
  *"merge --ff-only origin/master"*)
    exit "${FAKE_GIT_MERGE_CODIGO:-0}"
    ;;
  *"rev-parse HEAD"*)
    echo "${FAKE_SHA_ACTUAL:-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}"
    ;;
  *)
    echo "git-falso: subcomando no soportado: $*" >&2
    exit 97
    ;;
esac
''')

    _escribir_ejecutable(bin_dir / "curl", r'''#!/usr/bin/env bash
echo "curl $*" >> "$FAKE_LOG"
echo -n "${FAKE_HEALTH_CODIGO:-200}"
''')

    _escribir_ejecutable(bin_dir / "install", r'''#!/usr/bin/env bash
echo "install $*" >> "$FAKE_LOG"
if [ "${FAKE_INSTALL_FALLA:-0}" = "1" ]; then
  echo "install-falso: fallo simulado" >&2
  exit 1
fi
# Los ultimos dos argumentos son origen y destino -- ignora -m/-o/-g.
origen="${@: -2:1}"
destino="${@: -1}"
cp "$origen" "$destino"
''')

    _escribir_ejecutable(bin_dir / "systemd-analyze", r'''#!/usr/bin/env bash
echo "systemd-analyze $*" >> "$FAKE_LOG"
if [ "$1" = "verify" ]; then
  unidad="$(basename "$2")"
  for mala in ${FAKE_VERIFY_FALLA_UNIDADES:-}; do
    if [ "$unidad" = "$mala" ]; then
      echo "systemd-analyze-falso: $unidad no verifica" >&2
      exit 1
    fi
  done
  exit 0
fi
echo "systemd-analyze-falso: subcomando no soportado: $1" >&2
exit 96
''')

    _escribir_ejecutable(bin_dir / "sudo", r'''#!/usr/bin/env bash
echo "sudo $*" >> "$FAKE_LOG"
# El guion real solo usa `sudo -u <usuario> git ...` -- se descarta el
# `-u <usuario>` y se ejecuta el resto tal cual (no hace falta un usuario
# de verdad para las pruebas: el `git` que sigue tambien es falso).
if [ "$1" = "-u" ]; then
  shift 2
fi
exec "$@"
''')


@pytest.fixture
def entorno(tmp_path):
    """Arma el PATH falso, un checkout de producción de juguete (con las 3
    unidades reales del repo -- `systemd-analyze` es falso, así que su
    contenido no importa para la sintaxis, pero conviene que existan con
    nombres reales) y un `/etc/jax/.env` de juguete. Devuelve una función
    `correr(**overrides)` que ejecuta el guion REAL con ese entorno."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _armar_bin_falso(bin_dir)

    checkout = tmp_path / "checkout"
    unidades_dir = checkout / "ops" / "migration" / "systemd-units"
    unidades_dir.mkdir(parents=True)
    for nombre in ("jax-catalogo-modelos.service", "jax-catalogo-modelos.timer",
                   "jax-catalogo-modelos-aviso.service"):
        (unidades_dir / nombre).write_text(f"# unidad de prueba: {nombre}\n")

    env_falso = tmp_path / "fake.env"
    env_falso.write_text(
        "JAX_DB_HOST=127.0.0.1\nJAX_DB_PORT=3308\nJAX_DB_USER=fake\n"
        "JAX_DB_PASSWORD=fake\nJAX_DB_NAME=jax_memory_test_fake\n"
    )

    log = tmp_path / "log.txt"
    log.write_text("")

    destino_unidades = tmp_path / "etc-systemd-system"
    destino_unidades.mkdir()

    def correr(sha_esperado=SHA_ESPERADO, **overrides):
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
        env["FAKE_LOG"] = str(log)
        env["JAX_ENV_PATH"] = str(env_falso)
        env["JAX_CHECKOUT_PLATAFORMA"] = str(checkout)
        env["JAX_DESTINO_UNIDADES"] = str(destino_unidades)
        env["JAX_DUENIO_CHECKOUT"] = "nadie"  # el sudo falso lo descarta igual
        env["TOPE_ESPERA_SERVICIO_SEGUNDOS"] = "1"
        env["TOPE_ESPERA_HEALTH_SEGUNDOS"] = "1"
        env.update(overrides)
        resultado = subprocess.run(
            ["bash", str(SCRIPT), sha_esperado],
            env=env, capture_output=True, text=True, timeout=30,
        )
        return resultado, log.read_text()

    return correr


def setup_module(module):
    if not SCRIPT.is_file():
        raise AssertionError(f"falta el guion: {SCRIPT}")


def test_candado_viejo_tomado_aborta_sin_reiniciar(entorno):
    resultado, log = entorno(FAKE_CANDADO_VIEJO="47")
    assert resultado.returncode != 0
    assert "ABORTADO: no se reinició nada" in resultado.stderr
    assert "systemctl restart jax-platform" not in log


def test_candado_nuevo_calificado_tomado_aborta_sin_reiniciar(entorno):
    resultado, log = entorno(FAKE_CANDADO_NUEVO="52")
    assert resultado.returncode != 0
    assert "ABORTADO: no se reinició nada" in resultado.stderr
    assert "systemctl restart jax-platform" not in log


def test_fila_corriendo_aborta_sin_reiniciar(entorno):
    resultado, log = entorno(FAKE_TABLA_EXISTE="1", FAKE_FILAS_CORRIENDO="2")
    assert resultado.returncode != 0
    assert "ABORTADO: no se reinició nada" in resultado.stderr
    assert "sync manual sigue en curso" in resultado.stderr
    assert "systemctl restart jax-platform" not in log


def test_tabla_inexistente_y_candados_libres_sigue(entorno):
    """La tabla `catalogo_sync_ejecucion` no existe todavía (primer
    despliegue de esta función) -- con los candados libres, el guion tiene
    que SEGUIR (no abortar) y nunca intentar leer la tabla."""
    resultado, log = entorno(FAKE_TABLA_EXISTE="0")
    assert resultado.returncode == 0, resultado.stderr
    assert "systemctl restart jax-platform" in log
    assert "catalogo_sync_ejecucion WHERE estado" not in log


def test_salida_no_numerica_de_mariadb_aborta(entorno):
    """Una respuesta inesperada (ni 'NULL' ni un número) de cualquiera de
    las consultas tiene que abortar -- nunca asumir "0" o "libre" ante algo
    que no se pudo interpretar."""
    resultado, log = entorno(FAKE_TABLA_EXISTE="no-es-un-numero")
    assert resultado.returncode != 0
    assert "ABORTADO: no se reinició nada" in resultado.stderr
    assert "systemctl restart jax-platform" not in log


def test_conexion_a_mariadb_falla_aborta(entorno):
    resultado, log = entorno(FAKE_MARIADB_FALLA="1")
    assert resultado.returncode != 0
    assert "ABORTADO: no se reinició nada" in resultado.stderr
    assert "systemctl restart jax-platform" not in log


def test_sha_distinto_aborta_sin_reiniciar(entorno):
    resultado, log = entorno(FAKE_SHA_ACTUAL="b" * 40)
    assert resultado.returncode != 0
    assert "ABORTADO: no se reinició nada" in resultado.stderr
    assert "no coincide con el esperado" in resultado.stderr
    assert "systemctl restart jax-platform" not in log


def test_git_merge_falla_aborta_sin_reiniciar(entorno):
    resultado, log = entorno(FAKE_GIT_MERGE_CODIGO="1")
    assert resultado.returncode != 0
    assert "ABORTADO: no se reinició nada" in resultado.stderr
    assert "systemctl restart jax-platform" not in log


def test_verify_falla_no_instala_ninguna_unidad(entorno):
    """MINOR-2 (ronda anterior): verificar las TRES antes de instalar
    CUALQUIERA -- si la unidad del medio falla, la primera no debe quedar
    instalada tampoco. Este es un fallo DESPUÉS de reiniciar jax-platform
    (paso 5 ya pasó) -- el mensaje tiene que reflejar eso, no "no se
    reinició nada"."""
    resultado, log = entorno(
        FAKE_VERIFY_FALLA_UNIDADES="jax-catalogo-modelos.timer",
        FAKE_SHA_ACTUAL=SHA_ESPERADO,
    )
    assert resultado.returncode != 0
    assert "ABORTADO DESPUÉS de reiniciar jax-platform" in resultado.stderr
    assert "systemctl restart jax-platform" in log  # sí llegó a reiniciar
    assert "install " not in log  # pero NINGUNA unidad se instaló


def test_camino_feliz_reinicia_instala_las_tres_y_arranca_el_timer(entorno):
    resultado, log = entorno(FAKE_SHA_ACTUAL=SHA_ESPERADO)
    assert resultado.returncode == 0, resultado.stderr
    assert "systemctl stop jax-catalogo-modelos.timer" in log
    assert "systemctl restart jax-platform" in log
    for unidad in ("jax-catalogo-modelos.service", "jax-catalogo-modelos.timer",
                   "jax-catalogo-modelos-aviso.service"):
        assert f"systemd-analyze verify" in log
        assert unidad in log
    assert log.count("install ") == 3
    assert "systemctl daemon-reload" in log
    assert "systemctl start jax-catalogo-modelos.timer" in log
    assert "systemctl list-timers jax-catalogo-modelos.timer" in log
    assert "TODAVÍA" in resultado.stdout  # el resumen final de qué falta


def test_un_fallo_en_el_freno_autoritativo_nunca_llega_a_reiniciar(entorno):
    """El pedido explícito de la auditoría: un fallo en el paso 3 (freno
    autoritativo) no puede, bajo ninguna forma, llegar al paso 5 (reiniciar
    jax-platform) -- ni por un candado tomado, ni por una fila corriendo,
    ni por una respuesta inesperada. Se prueban las tres causas en un solo
    test para dejar constancia de que NINGUNA llega."""
    for overrides in (
        {"FAKE_CANDADO_VIEJO": "1"},
        {"FAKE_TABLA_EXISTE": "1", "FAKE_FILAS_CORRIENDO": "5"},
        {"FAKE_TABLA_EXISTE": "algo-raro"},
    ):
        resultado, log = entorno(**overrides)
        assert resultado.returncode != 0
        assert "systemctl restart jax-platform" not in log, overrides
