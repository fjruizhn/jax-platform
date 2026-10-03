"""Tope de REPROCESOS simultaneos de documentos de proyecto (jax-platform#186).

POR QUE UN CUPO PROPIO. Reprocesar un documento sin ficha recorre y hashea todo `fuente/` del proyecto
(medido el 2026-10-03 con 20.000 archivos: 0,23 s solo, ~16 s con 4 a la vez y el chat y la lista corriendo,
y con el cupo de subir compartido dejaba sin cupo a todas las subidas). Un reproceso lento no puede
quitarle lugar a las subidas: este cupo es APARTE del de `cupo_de_subidas`, con estado propio.

CUANTO. `proyectos.documentos.reprocesar_por_usuario` y `proyectos.documentos.reprocesar_globales` en
`axioma_config` (ajustes.py; las siembra db/migrations.py con 1 y 1: como mucho un recorrido completo a
la vez). Se leen en cada pedido, con el cache de `ajustes`.

ESTADO. En memoria de UN proceso, mecanismo de `cupo.Cupo`. El endpoint lo toma ANTES del recorrido y lo
suelta en un `finally` (responde 429 `reprocesos_simultaneos`, con `Retry-After`, si no hay lugar).
"""
from __future__ import annotations

from proyectos_documentos.cupo import Cupo

_cupo = Cupo()


def tomar(usuario: str, *, por_usuario: int, globales: int) -> bool:
    return _cupo.tomar(usuario, por_usuario=por_usuario, globales=globales)


def soltar(usuario: str) -> None:
    try:
        _cupo.soltar(usuario)
    except RuntimeError:
        raise RuntimeError(f"cupo de reprocesos: soltar sin tomar ({usuario!r})") from None


def tomar_usuario(usuario: str, *, por_usuario: int) -> bool:
    return _cupo.tomar_usuario(usuario, por_usuario=por_usuario)


def soltar_usuario(usuario: str) -> None:
    _cupo.soltar_usuario(usuario)


def tomar_global(*, globales: int) -> bool:
    return _cupo.tomar_global(globales=globales)


def soltar_global() -> None:
    _cupo.soltar_global()


def en_uso() -> tuple[int, dict[str, int]]:
    return _cupo.en_uso()
