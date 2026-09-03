"""
Revalidación por mtime del contexto de gobernanza (post-SP3, 2026-09-03).

Defecto que estos tests fijan — el "régimen 2" medido el 2026-09-03: con el
proceso ya caliente, `validation_context()` servía el contexto cacheado por
`lru_cache(maxsize=1)` para siempre. Si `las_manos/config.toml` se volvía
ilegible DESPUÉS del primer turno, no pasaba nada visible: el snapshot se
seguía construyendo con datos que ya no estaban en disco, se inyectaba en el
prompt y los claims que lo citaban se acreditaban como OBSERVADO. El snapshot
mentía y ninguna de las dos capas podía notarlo, porque ambas leen el MISMO
objeto cacheado (esa es la causa concreta de la propiedad diferida del §9.4
del spec de SP3).

Lo que se fija acá: un `stat()` por construcción; si cambió el mtime de alguno
de los tres archivos fuente, el contexto se reconstruye. Con la config rota eso
convierte el régimen 2 en el régimen 1 —fallo ruidoso y visible— en vez de
silencio.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

import governance_context
import http_client
from tests.test_chat_contract_wrapper import _FakeResponse

CLAIM = ('{"claim": [{"predicate": "CAPABILITY_AVAILABLE", "args": {"name": "write_file", '
         '"mode": "mutating"}, "evidence_pointer": "/capabilities/10"}], '
         '"analysis": "a", "judgment": null}')


@pytest.fixture
def repo_copia(tmp_path):
    """Copia de los archivos que sigue JAX_REPO, para romperlos SIN tocar el
    repo real. Deja el módulo como estaba al salir."""
    destino = tmp_path / "jax"
    (destino / "las_manos").mkdir(parents=True)
    shutil.copy(governance_context.JAX_REPO / "las_manos" / "config.toml",
                destino / "las_manos" / "config.toml")
    anterior = governance_context.JAX_REPO
    governance_context.JAX_REPO = destino
    governance_context.validation_context.cache_clear()
    try:
        yield destino
    finally:
        governance_context.JAX_REPO = anterior
        governance_context.validation_context.cache_clear()


def _romper(config: Path) -> None:
    """Rompe el TOML en su MISMA ruta y adelanta el mtime un segundo entero.
    El adelanto explícito es determinismo, no necesidad: escribir ya cambia el
    mtime en ns. Fija el mecanismo que se está probando."""
    config.write_text("[[[ TOML roto a proposito\n", encoding="utf-8")
    st = os.stat(config)
    os.utime(config, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def test_sin_cambios_en_disco_el_contexto_no_se_reconstruye(repo_copia):
    primero = governance_context.validation_context()
    assert governance_context.validation_context() is primero


def test_config_rota_en_caliente_invalida_el_cache_y_falla_ruidoso(repo_copia):
    governance_context.validation_context()          # caliente, con la config buena
    _romper(repo_copia / "las_manos" / "config.toml")
    import tomllib
    with pytest.raises(tomllib.TOMLDecodeError):     # antes del fix: devolvía el contexto viejo
        governance_context.validation_context()


def test_config_rota_en_caliente_da_SnapshotError_no_un_snapshot_que_miente(repo_copia):
    import api.chat as chat
    import grounding as governance_grounding

    governance_context.validation_context()
    assert isinstance(chat._build_grounding(), governance_grounding.Snapshot)
    _romper(repo_copia / "las_manos" / "config.toml")
    resultado = chat._build_grounding()
    assert isinstance(resultado, governance_grounding.SnapshotError)
    assert "TOMLDecodeError" in resultado.reason


class _FakePost:
    def __init__(self):
        self.payloads = []

    async def post(self, url, **kw):
        if "/motor/authorize-facet" in url:
            return _FakeResponse({"allowed": True, "reason": "OK"})
        self.payloads.append(kw.get("json"))
        return _FakeResponse({"choices": [{"message": {"content": CLAIM}}],
                              "usage": {"prompt_tokens": 1, "completion_tokens": 1}})


async def _fila(shadow_message_id):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT grounding_snapshot_sha256, validated_at FROM shadow_messages "
                "WHERE shadow_message_id = %s", (shadow_message_id,))
            mensaje = await cur.fetchone()
            await cur.execute(
                "SELECT COUNT(*) FROM shadow_claim_verdicts WHERE shadow_message_id = %s",
                (shadow_message_id,))
            return mensaje, (await cur.fetchone())[0]


def test_turno_completo_con_config_rota_en_caliente_queda_ERROR_y_sin_veredictos(client, repo_copia):
    """La provocación del 2026-09-03, ahora como test: proceso caliente, se rompe
    la config, se dispara el endpoint real. Esperado = el régimen de caché fría."""
    import grounding as governance_grounding
    from auth.jwt import create_access_token
    from shadow_validation import run_shadow_validation

    governance_context.validation_context()          # caliente y sana
    _romper(repo_copia / "las_manos" / "config.toml")

    token = create_access_token("1", "1", "operator")
    fake = _FakePost()
    capturado = {}
    original = http_client._client
    http_client._client = fake
    try:
        with patch("jax_engine.background.add_safe_task",
                   side_effect=lambda bg, fn, *args: capturado.__setitem__("args", args)):
            respuesta = client.post(
                "/api/chat",
                json={"message": "¿Qué capabilities de escritura tiene hoy este sistema?",
                      "facet": "jekyll"},
                headers={"Authorization": f"Bearer {token}"})
    finally:
        http_client._client = original

    # 1. el usuario no ve nada raro: el grounding es medición, no puede tumbar un chat
    assert respuesta.status_code == 200, respuesta.text
    # 2. el prompt sale SIN el bloque de hechos (no se inyecta lo que ya no está en disco)
    encabezado = governance_grounding.render(
        governance_grounding.Snapshot(entries=(), canonical_json="{}", sha256="0" * 64)
    ).splitlines()[0]
    assert encabezado not in fake.payloads[0]["messages"][0]["content"]
    # 3. lo que viaja al validador es la marca, no un snapshot que miente
    conv_uuid, smid, facet, contract, grounding, origin = capturado["args"]
    assert isinstance(grounding, governance_grounding.SnapshotError)

    # 4. la fila queda marcada ERROR y sin veredictos: la task muere fail-closed
    #    releyendo la misma config rota, igual que con la caché fría.
    import tomllib
    with pytest.raises(tomllib.TOMLDecodeError):
        client.portal.call(run_shadow_validation, conv_uuid or "conv-revalidacion",
                           smid, facet, contract, grounding, origin)
    (sha, validated_at), veredictos = client.portal.call(
        _fila, smid)
    assert sha == "ERROR"
    assert validated_at is None
    assert veredictos == 0
