"""Las dos copias del contrato de idempotencia no pueden derivar (jax-platform#210, ronda 3).

La tabla `CONTRATO_IDEMPOTENCIA` de la falsa de LAS MANOS (tests/test_proyectos_documentos_despachador.py) es una
COPIA de la que jax prueba contra la ruta real (las_manos/_procesamiento_idempotencia_db_test.py). Este test lee
el archivo de jax en `$JAX_REPO_PATH`, extrae la tabla con `ast.literal_eval` (SIN importarlo: no ejecuta codigo de
otro repo) y exige que sean iguales; hace lo mismo con los estados que LAS MANOS reintenta
(`_ESTADOS_QUE_SE_REINTENTAN` de procesamiento_routes.py). Falla CERRADO: sin JAX_REPO_PATH, o sin el archivo, no
hay nada que comparar y eso es un rojo, no un skip. Si el pin de jax del CI es anterior al contrato, hay que subirlo.
"""
import ast
import os
from pathlib import Path

from tests.test_proyectos_documentos_despachador import CONTRATO_IDEMPOTENCIA, ESTADOS_QUE_SE_REINTENTAN


def _raiz_de_jax() -> Path:
    crudo = os.environ.get("JAX_REPO_PATH")
    assert crudo, "JAX_REPO_PATH no esta definido: sin el repo de jax no se puede comparar el contrato de idempotencia"
    return Path(crudo)


def _asignacion(arbol: ast.AST, nombre: str) -> ast.expr:
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Assign) and any(isinstance(t, ast.Name) and t.id == nombre for t in nodo.targets):
            return nodo.value
    raise AssertionError(f"{nombre} no esta en el archivo de jax")


def _leer(ruta: Path) -> ast.AST:
    assert ruta.is_file(), (f"{ruta} no existe: el JAX_REPO_PATH de este job es anterior al contrato de idempotencia "
                            "de LAS MANOS; subir el pin de jax a un commit que lo incluya")
    return ast.parse(ruta.read_text(encoding="utf-8"))


def test_la_tabla_de_contrato_es_la_misma_que_la_de_jax():
    arbol = _leer(_raiz_de_jax() / "las_manos" / "_procesamiento_idempotencia_db_test.py")
    de_jax = ast.literal_eval(_asignacion(arbol, "CONTRATO_IDEMPOTENCIA"))
    assert tuple(de_jax) == tuple(CONTRATO_IDEMPOTENCIA), (
        "la tabla de contrato de la falsa de LAS MANOS derivo de la de jax: cambiar las dos juntas")


def test_los_estados_que_se_reintentan_son_los_de_jax():
    arbol = _leer(_raiz_de_jax() / "las_manos" / "procesamiento_routes.py")
    valor = _asignacion(arbol, "_ESTADOS_QUE_SE_REINTENTAN")
    # frozenset({JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.REJECTED}) -> {"failed", "cancelled", "rejected"}
    estados = {n.attr.lower() for n in ast.walk(valor) if isinstance(n, ast.Attribute)}
    assert estados and estados == set(ESTADOS_QUE_SE_REINTENTAN), (estados, ESTADOS_QUE_SE_REINTENTAN)


def test_sin_jax_repo_path_el_control_falla_cerrado(monkeypatch):
    import pytest
    monkeypatch.delenv("JAX_REPO_PATH", raising=False)
    with pytest.raises(AssertionError, match="JAX_REPO_PATH"):
        _raiz_de_jax()
