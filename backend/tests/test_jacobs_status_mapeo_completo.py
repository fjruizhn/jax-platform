"""Ronda `feat/estado-disputed` (2026-09-18): `disputed` es el segundo estado
terminal nuevo que llega desde el repo `jax` (el primero fue `expired`, T4
2026-08-19) sin entrada en `_JACOBS_STATUS_MAP` (jax_engine/state.py) -- el
MISMO defecto exacto, dos veces. `.get(jacobs_status, "running")` deja un
estado sin mapear pegado en "running" para siempre: nunca sale de
`active_pipelines`, el cupo del tenant se fuga, y `encolar_aviso_fin_pipeline`
nunca se llama.

El agujero real no es "faltaba disputed": es que el mapa se puede quedar
corto EN SILENCIO, sin que nada lo note hasta que un pipeline real se queda
pegado en producción. Este test no fija una copia local del enum de Jacobs
(una copia se puede escribir mal o quedar vieja sin que nadie la revise):
importa el `PipelineStatus` REAL del repo `jax` vía `JAX_REPO_PATH`, el mismo
mecanismo que ya usa `test_chat_contract_prompt.py` para el vocabulario de
predicados (ver ese archivo). Corre en los dos jobs de CI -- con DB y sin DB
-- porque los dos clonan `jax` y setean `JAX_REPO_PATH` (.github/workflows/
policy.yml, jobs backend-tests-con-db y backend-tests-no-db).

Si mañana `jax` agrega un estado terminal nuevo (otro "disputed"), este test
se pone rojo el día que esta rama se corre contra un `jax` que ya lo tiene --
antes de que un pipeline real se quede pegado, no después.
"""
import os
import sys
from pathlib import Path
from typing import get_args

sys.path.insert(0, str(Path(os.environ["JAX_REPO_PATH"])))
from jacobs.models import PipelineStatus as JacobsPipelineStatus  # noqa: E402

from jax_engine.schemas import PipelineStatus as PanelPipelineStatus  # noqa: E402
from jax_engine.state import _JACOBS_STATUS_MAP  # noqa: E402


def test_todos_los_estados_de_jacobs_tienen_entrada_en_el_mapa():
    faltantes = sorted(s.value for s in JacobsPipelineStatus if s.value not in _JACOBS_STATUS_MAP)
    assert faltantes == [], (
        "jacobs.models.PipelineStatus (repo jax) tiene estados sin entrada en "
        f"_JACOBS_STATUS_MAP (jax_engine/state.py): {faltantes}. Sin ella, "
        '.get(jacobs_status, "running") deja el pipeline pegado en "running" '
        "para siempre -- mismo defecto que tuvo `expired` hasta el 2026-09-18."
    )


def test_cada_valor_mapeado_es_un_status_valido_del_panel():
    validos = set(get_args(PanelPipelineStatus))
    for crudo, mapeado in _JACOBS_STATUS_MAP.items():
        assert mapeado in validos, (
            f'_JACOBS_STATUS_MAP["{crudo}"] = "{mapeado}", que no es un '
            f"PipelineStatus válido del panel ({sorted(validos)}, "
            "jax_engine/schemas.py)."
        )
