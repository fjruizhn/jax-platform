# backend/tests/test_ejecutor_reglas_envoltorios.py
"""C1 contra envoltorios (2026-09-17). Puros: no piden la DB.

En la misión real contra la VM desechable el cerebro corrió
`tmux new-session -d … 'ssh …'`: `ssh_sin_tt` no lo ve (el ssh va entre comillas) y el
lector de destinos de jax tampoco (el programa del segmento es tmux). Lo atrapó C5, no C1.

Acá se prueba la semilla con `re` y el campo de cada regla. La prueba completa —con el
lector de destinos y el inventario, la misma autoprueba que corre el gancho en cada
arranque— está en jax (`tests/test_ejecutor_politica_db.py`, contra estas migraciones).
"""
import json
import re
from pathlib import Path

import pytest

_DB = Path(__file__).resolve().parents[1] / "db"
_NUEVAS = json.loads((_DB / "semilla_ejecutor_reglas_envoltorios.json").read_text(encoding="utf-8"))
_V1 = json.loads((_DB / "semilla_ejecutor_reglas.json").read_text(encoding="utf-8"))
_POR_CODIGO = {r["codigo"]: r for r in _NUEVAS}

# Literal del registro de C3 (/var/log/jax-ejecutor/registro.jsonl, 2026-09-17 ~08:35).
EVASION_REAL = ("tmux new-session -d -s ssh-test 'ssh -p 58291 axioma@192.168.122.50 hostname; sleep 2' "
                "&& tmux capture-pane -t ssh-test -p && tmux kill-session -t ssh-test")

# Trabajo razonable que ninguna regla de envoltorio puede bloquear (decisiones en jax CONTEXT.md).
LEGITIMOS = [
    "ssh -tt -p 58291 axioma@192.168.122.50 hostname",
    "ssh -tt -p 58291 axioma@192.168.122.50 df -h /",
    "ssh -tt -p 58291 axioma@192.168.122.50 free -h",
    "ssh -tt -p 58291 axioma@atemai 'tmux ls'",
    "ssh -tt -p 58291 axioma@atemai 'screen -ls'",
    "ssh -tt axioma@atemai 'ps aux | grep -E \"tmux|screen|nohup\"'",
    "ssh -tt axioma@atemai 'systemctl status atd; atq'",
    "sudo bash -c 'df -h > /tmp/df.txt'",
    "ssh -tt axioma@atemai 'bash -lc uptime'",
    "grep -r setsid /etc/init.d",
    "man systemd-run | head",
    "git log --format='%h %s' -3",
    "journalctl -u nginx --since '1 hour ago' | tail",
]


def _texto(regla, tool_input):
    if regla["campo"] == "command":
        return tool_input.get("command")
    if regla["campo"] == "file_path":
        return next((tool_input[c] for c in ("file_path", "notebook_path", "path") if isinstance(tool_input.get(c), str)), None)
    # Igual que jax.ejecutor.contratos.politica._texto_de para `cualquiera`.
    return json.dumps(tool_input, sort_keys=True, ensure_ascii=False)


def _bloquea(regla, tool_name, tool_input):
    texto = _texto(regla, tool_input)
    return (re.fullmatch(regla["herramientas"], tool_name) is not None
            and texto is not None and re.search(regla["patron"], texto) is not None)


def test_la_semilla_nueva_trae_los_siete_envoltorios():
    assert set(_POR_CODIGO) == {
        "envoltorio_tmux_screen", "envoltorio_desacopla", "envoltorio_script_c", "envoltorio_at_batch",
        "envoltorio_systemd_run", "envoltorio_ssh_escondido", "sandbox_desactivado"}


@pytest.mark.parametrize("codigo", sorted(_POR_CODIGO))
def test_cada_regla_pasa_sus_propios_ejemplos(codigo):
    r = _POR_CODIGO[codigo]
    assert r["ejemplos_coincide"], "una regla sin ejemplo que coincida no se vio bloquear"
    for e in r["ejemplos_coincide"]:
        assert _bloquea(r, e["tool_name"], e["tool_input"]), e
    for e in r["ejemplos_no_coincide"]:
        assert not _bloquea(r, e["tool_name"], e["tool_input"]), e


def test_forma_compatible_con_la_tabla_y_con_la_semilla_v1():
    assert not set(_POR_CODIGO) & {r["codigo"] for r in _V1}
    for r in _NUEVAS:
        assert r["tipo"] == "prohibido" and r["es_canario"] is False
        assert r["campo"] in ("command", "file_path", "cualquiera")
        assert len(r["codigo"]) <= 80 and len(r["patron"]) <= 1000 and len(r["origen"]) <= 300
        assert len(r["herramientas"]) <= 200 and r["ambito_host"] is None and r["ambito_roles"] == []
        re.compile(r["patron"])


def test_la_evasion_real_queda_bloqueada():
    assert _bloquea(_POR_CODIGO["envoltorio_tmux_screen"], "Bash", {"command": EVASION_REAL})


def test_dangerously_disable_sandbox_bloquea_solo_cuando_es_true():
    r = _POR_CODIGO["sandbox_desactivado"]
    assert _bloquea(r, "Bash", {"command": "uptime", "dangerouslyDisableSandbox": True})
    assert not _bloquea(r, "Bash", {"command": "uptime", "dangerouslyDisableSandbox": False})
    assert not _bloquea(r, "Bash", {"command": "uptime"})


@pytest.mark.parametrize("comando", LEGITIMOS)
def test_trabajo_legitimo_no_lo_bloquea_ningun_envoltorio(comando):
    assert [r["codigo"] for r in _NUEVAS if _bloquea(r, "Bash", {"command": comando})] == []


def test_run_migrations_siembra_los_envoltorios_despues_de_la_semilla_v1():
    import inspect

    from db import migrations
    fuente = inspect.getsource(migrations.run_migrations)
    v1 = fuente.find("await _ejecutor_reglas_v1(cur)")
    nuevas = fuente.find("await _ejecutor_reglas_envoltorios_v1(cur)")
    assert 0 <= v1 < nuevas, "la migración de envoltorios no corre en el arranque (o corre antes que la semilla v1)"
