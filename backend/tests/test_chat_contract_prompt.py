"""
El prompt de contrato tiene que NOMBRAR el vocabulario cerrado.

Medido en produccion el 2026-09-01, contra `shadow_messages` de la
MariaDB real (jax_memory, :3308): 22 de 22 mensajes con `has_claim = 0`,
en 5 facetas distintas, del 2026-08-18 al 2026-09-01. `contract_parsed`
era 1 en 18 de esos 22 -- el contrato parseaba bien, `analysis` y
`judgment` llegaban; lo unico que nunca llego fue el `claim`.
`shadow_claim_verdicts` nunca tuvo una sola fila.

La causa no era el parser: era el prompt. `_CONTRACT_PROMPT_SUFFIX` pedia
"lista de afirmaciones verificables (puede ir vacia: [])" sin nombrar
ninguno de los ocho predicados de policy/vocabulary/predicates.yaml. El
modelo no tenia forma de saber que nombre es legal, y el propio prompt le
ofrecia la salida vacia. Devolver [] siempre era la respuesta correcta al
prompt que le estabamos dando.

Estos tests fijan las dos propiedades que lo cierran: el prompt nombra
los ocho predicados con su firma de args, y esa lista se GENERA desde el
YAML -- si alguien agrega un predicado al vocabulario y el prompt no lo
sigue, esto se pone rojo.
"""
import os
import sys
from pathlib import Path

import pytest

from api.chat import _CONTRACT_PROMPT_SUFFIX, _render_contract_suffix

sys.path.insert(
    0,
    str(Path(os.getenv("JAX_REPO_PATH", os.path.expanduser("~/jax"))) / "policy" / "governance"),
)
import loaders as governance_loaders  # noqa: E402


@pytest.fixture(scope="module")
def predicates():
    return governance_loaders.load_predicates()


def test_contract_suffix_nombra_los_ocho_predicados(predicates):
    faltantes = [name for name in predicates if name not in _CONTRACT_PROMPT_SUFFIX]
    assert faltantes == [], f"el prompt no nombra estos predicados: {faltantes}"


def test_contract_suffix_declara_los_args_de_cada_predicado(predicates):
    for spec in predicates.values():
        linea = next(
            (l for l in _CONTRACT_PROMPT_SUFFIX.splitlines() if spec.name in l), None
        )
        assert linea is not None, f"{spec.name} no aparece en ninguna linea"
        for arg in spec.args:
            assert arg in linea, f"{spec.name}: falta el arg '{arg}' en su firma ({linea!r})"


def test_contract_suffix_se_genera_desde_el_spec_y_no_esta_hardcodeado():
    """Si alguien copia la lista al prompt a mano, este test se pone rojo:
    un predicado inventado que solo existe en el spec tiene que aparecer,
    y uno real que NO esta en el spec no tiene que aparecer."""
    inventado = governance_loaders.PredicateSpec(
        name="PREDICADO_DE_PRUEBA",
        args=("alfa", "beta"),
        source_of_truth="Ninguna, es de prueba",
    )
    rendered = _render_contract_suffix({inventado.name: inventado})
    assert "PREDICADO_DE_PRUEBA" in rendered
    assert "alfa" in rendered and "beta" in rendered
    assert "CAPABILITY_AVAILABLE" not in rendered


def test_contract_suffix_no_ofrece_la_lista_vacia_como_default():
    """La redaccion medida como causa. `[]` sigue siendo legal -- inventar
    claims es peor que no emitirlos -- pero deja de ser lo que el prompt
    ofrece primero."""
    assert "puede ir vacía: []" not in _CONTRACT_PROMPT_SUFFIX


def test_contract_suffix_admits_evidence_pointer_and_no_longer_forbids_it():
    # Regresión (spec §6.3 / §9.3): hasta SP3 el sufijo decía "SOLO esos dos
    # campos, nada más" y "No incluyas ningún otro campo" -- el modelo NO
    # PODÍA citar. Restaurar cualquiera de las dos frases pone rojo.
    assert "SOLO esos dos campos" not in _CONTRACT_PROMPT_SUFFIX
    assert "No incluyas ningún otro campo" not in _CONTRACT_PROMPT_SUFFIX
    assert '"evidence_pointer"' in _CONTRACT_PROMPT_SUFFIX


def test_parser_keeps_evidence_pointer_and_model_declared_authority():
    from api.chat import _parse_contract_response
    raw = ('{"claim": [{"predicate": "CAPABILITY_AVAILABLE", "args": {"name": "x", "mode": "read_only"}, '
           '"evidence_pointer": "/capabilities/3", "authority": "EJECUTADO", "otro": 1}], '
           '"analysis": "a", "judgment": null}')
    r = _parse_contract_response(raw)
    assert r.contract_parsed is True
    assert r.claims == [{
        "predicate": "CAPABILITY_AVAILABLE", "args": {"name": "x", "mode": "read_only"},
        "evidence_pointer": "/capabilities/3", "authority": "EJECUTADO",
    }]  # "otro" se descarta; evidence_pointer y authority se conservan (spec §9.1)


def test_parser_keeps_non_string_evidence_pointer_for_accredit_to_reject():
    # No se degrada el contrato por un puntero raro: eso es POINTER_MISMATCH
    # o FACT_NOT_IN_SNAPSHOT en shadow validation (spec §9.1b; repo jax,
    # 2026-09-03), no un contrato roto.
    from api.chat import _parse_contract_response
    raw = '{"claim": [{"predicate": "P", "args": {}, "evidence_pointer": 7}], "analysis": "a"}'
    r = _parse_contract_response(raw)
    assert r.contract_parsed is True
    assert r.claims[0]["evidence_pointer"] == 7
