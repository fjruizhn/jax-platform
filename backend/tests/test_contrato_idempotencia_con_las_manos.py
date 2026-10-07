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
import subprocess
from pathlib import Path

from tests.test_proyectos_documentos_despachador import CONTRATO_IDEMPOTENCIA, ESTADOS_QUE_SE_REINTENTAN


PRODUCCION = Path("/srv/jax-prod")


def _raiz_de_jax() -> Path:
    crudo = os.environ.get("JAX_REPO_PATH")
    assert crudo, "JAX_REPO_PATH no esta definido: sin el repo de jax no se puede comparar el contrato de idempotencia"
    raiz = Path(crudo)
    resuelta = raiz.resolve()
    assert resuelta != PRODUCCION and PRODUCCION not in resuelta.parents, (
        f"JAX_REPO_PATH={crudo} resuelve bajo {PRODUCCION}: el contrato se compara contra un arbol de desarrollo "
        "(el pin de CI o un worktree), nunca contra produccion. Apunta JAX_REPO_PATH a un checkout de jax con la rama "
        "de idempotencia.")
    return raiz


def _version(raiz: Path) -> str:
    """`git rev-parse HEAD` del arbol que se leyo, para que un rojo diga CONTRA QUE se comparo."""
    try:
        return subprocess.run(["git", "-C", str(raiz), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10, check=True).stdout.strip()
    except Exception as exc:  # fail-soft: es solo el texto del mensaje; sin git el rojo igual se da, con "sin HEAD"
        return f"sin HEAD ({type(exc).__name__})"


def _asignacion(arbol: ast.AST, nombre: str) -> ast.expr:
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Assign) and any(isinstance(t, ast.Name) and t.id == nombre for t in nodo.targets):
            return nodo.value
    raise AssertionError(f"{nombre} no esta en el archivo de jax")


def _leer(raiz: Path, relativa: str) -> tuple[ast.AST, str]:
    ruta = raiz / relativa
    version = _version(raiz)
    assert ruta.is_file(), (f"{ruta} no existe (jax en {version}): el JAX_REPO_PATH de este job es anterior al contrato "
                            "de idempotencia de LAS MANOS; subir el pin de jax a un commit que lo incluya")
    return ast.parse(ruta.read_text(encoding="utf-8")), version


def test_la_tabla_de_contrato_es_la_misma_que_la_de_jax():
    arbol, version = _leer(_raiz_de_jax(), "las_manos/_procesamiento_idempotencia_db_test.py")
    de_jax = ast.literal_eval(_asignacion(arbol, "CONTRATO_IDEMPOTENCIA"))
    assert tuple(de_jax) == tuple(CONTRATO_IDEMPOTENCIA), (
        f"la tabla de contrato de la falsa de LAS MANOS derivo de la de jax (leida en jax {version}): "
        "cambiar las dos juntas")


def test_los_estados_que_se_reintentan_son_los_de_jax():
    arbol, version = _leer(_raiz_de_jax(), "las_manos/procesamiento_routes.py")
    valor = _asignacion(arbol, "_ESTADOS_QUE_SE_REINTENTAN")
    # frozenset({JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.REJECTED}) -> {"failed", "cancelled", "rejected"}
    estados = {n.attr.lower() for n in ast.walk(valor) if isinstance(n, ast.Attribute)}
    assert estados and estados == set(ESTADOS_QUE_SE_REINTENTAN), (estados, ESTADOS_QUE_SE_REINTENTAN, f"jax {version}")


def test_sin_jax_repo_path_el_control_falla_cerrado(monkeypatch):
    import pytest
    monkeypatch.delenv("JAX_REPO_PATH", raising=False)
    with pytest.raises(AssertionError, match="JAX_REPO_PATH"):
        _raiz_de_jax()


def test_el_contrato_no_se_lee_de_produccion(monkeypatch):
    import pytest
    monkeypatch.setenv("JAX_REPO_PATH", "/srv/jax-prod/jax")
    with pytest.raises(AssertionError, match="produccion"):
        _raiz_de_jax()


def test_el_rojo_dice_contra_que_version_de_jax_se_comparo(tmp_path, monkeypatch):
    import pytest
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-q",
                    "--allow-empty", "-m", "x"], check=True)
    cabeza = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], capture_output=True, text=True,
                            check=True).stdout.strip()
    monkeypatch.setenv("JAX_REPO_PATH", str(tmp_path))
    with pytest.raises(AssertionError, match=cabeza):
        _leer(_raiz_de_jax(), "las_manos/_procesamiento_idempotencia_db_test.py")
