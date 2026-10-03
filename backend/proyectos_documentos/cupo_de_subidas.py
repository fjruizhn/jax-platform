"""Tope de subidas SIMULTANEAS de documentos de proyecto (E2a, ronda final, MAJOR-4).

POR QUE. Un CONTRIBUTOR podia abrir N POST de 1 GiB a la vez: Starlette vuelca cada
multipart a TMPDIR mientras lo lee, antes de cualquier tope por archivo o por lote.

MISMO PATRON QUE /api/chat/upload (adjuntos/limite_de_subidas.py): estado en memoria del
proceso, cifras leidas en cada pedido, y el 429 sale SIN leer el cuerpo. Dos diferencias,
a proposito:
  - Aquel cuenta subidas POR MINUTO (ventana deslizante); esto cuenta subidas EN VUELO, que
    es lo que llena TMPDIR. Una ventana deslizante no suelta el cupo al terminar la subida.
  - Aquel es un middleware ASGI porque /api/chat/upload declara parametros de formulario y
    FastAPI lee el multipart ANTES de resolver dependencias. `subir` no declara ninguno:
    recibe `Request` y llama a `request.form()` ella misma, asi que el cupo se toma en el
    handler, despues de la autorizacion y antes de leer el cuerpo (lo fija
    tests/test_proyectos_documentos_api.py::test_tercera_subida_simultanea_del_mismo_usuario_429_sin_leer_el_cuerpo).

CUANTO. `proyectos.documentos.subidas_por_usuario` y `proyectos.documentos.subidas_globales`
en `axioma_config` (ajustes.py; siembra db/migrations.py). Se leen en cada subida, con el
cache de `ajustes`: bajar una cifra no corta lo que ya esta en vuelo, solo frena lo nuevo.

ESTADO. En memoria de UN proceso (ajustes.py exige un solo worker); el mecanismo es `cupo.Cupo`, que
comparte con `cupo_de_reprocesos` SIN compartir estado. El llamador suelta en un `finally`: tambien si
la subida falla o se cancela.
"""
from __future__ import annotations

from proyectos_documentos.cupo import Cupo

_cupo = Cupo()


def tomar(usuario: str, *, por_usuario: int, globales: int) -> bool:
    """True y el cupo queda tomado; False si el usuario o el servicio ya estan en su tope."""
    return _cupo.tomar(usuario, por_usuario=por_usuario, globales=globales)


def soltar(usuario: str) -> None:
    try:
        _cupo.soltar(usuario)
    except RuntimeError:
        raise RuntimeError(f"cupo de subidas: soltar sin tomar ({usuario!r})") from None


def en_uso() -> tuple[int, dict[str, int]]:
    """(total, por usuario) en vuelo, para las pruebas y el diagnostico."""
    return _cupo.en_uso()
