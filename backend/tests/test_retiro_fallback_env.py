"""
B1.4 — retiro del fallback a variables de entorno (2026-09-17).

La ventana de doble lectura DB->env cumplió su criterio de salida: 30 días de
journal de jax-platform, 2.760 líneas `source=db`, **0 líneas
`source=env_fallback`**, con una rotación real de la llave de Gemini el
2026-09-15 dentro de la ventana. Decisión de Fernando: se retira el fallback.

Estos tests son el control del retiro. Contra el código VIEJO fallan:
`resolve_credential_instrumented` devolvía el valor de la env var cuando la DB
no tenía credencial activa. Contra el código NUEVO, sin credencial en la DB se
levanta `CredentialUnavailableError` — FAIL-CLOSED, nunca fail-open.
"""
import importlib
import pathlib
import subprocess

import pytest

import credential_resolver
from credential_resolver import CredentialUnavailableError

# (módulo consumidor, provider_id, env var que ANTES lo rescataba)
CONSUMIDORES = [
    ("api.image", "openai", "OPENAI_API_KEY"),
    ("facet_resolver", "deepseek", "DEEPSEEK_API_KEY"),
    ("model_catalog", "gemini", "GEMINI_API_KEY"),
    ("model_catalog", "moonshot", "KIMI_API_KEY"),
    ("facet_resolver", "zhipu", "ZAI_API_KEY"),
]


def _resolvedor_del_modulo(nombre_modulo: str):
    """La función con la que ESE módulo resuelve credenciales, se llame como
    se llame. Así el control mide comportamiento (¿cae a la env var?) y no el
    nombre del símbolo: contra el árbol viejo agarra la instrumentada, contra
    el nuevo agarra `resolve_credential`."""
    mod = importlib.import_module(nombre_modulo)
    for nombre in ("resolve_credential_instrumented", "resolve_credential"):
        f = getattr(mod, nombre, None)
        if f is not None:
            return f
    pytest.fail(f"{nombre_modulo} no expone ningún resolvedor de credenciales")


@pytest.fixture(autouse=True)
def _cache_limpio():
    credential_resolver._cache.clear()
    yield
    credential_resolver._cache.clear()


@pytest.mark.parametrize("nombre_modulo,provider_id,env_key", CONSUMIDORES)
async def test_sin_credencial_en_db_no_cae_a_la_env_var(
    monkeypatch, nombre_modulo, provider_id, env_key
):
    """Credencial ausente en la DB + env var presente => FAIL-CLOSED.

    Contra el código viejo este test FALLA: devolvía 'valor-de-la-env-var'.
    """
    async def sin_credencial_activa(_pid):
        raise CredentialUnavailableError(f"no active credential for {_pid}")

    monkeypatch.setattr(
        credential_resolver, "_query_active_credential", sin_credencial_activa
    )
    monkeypatch.setenv(env_key, "valor-de-la-env-var")

    resolvedor = _resolvedor_del_modulo(nombre_modulo)
    with pytest.raises(CredentialUnavailableError):
        await resolvedor(provider_id)


async def test_resolve_credential_no_lee_el_entorno_en_ningun_caso(monkeypatch):
    """Ni siquiera con las 5 variables puestas a la vez."""
    async def sin_credencial_activa(_pid):
        raise CredentialUnavailableError("no active credential")

    monkeypatch.setattr(
        credential_resolver, "_query_active_credential", sin_credencial_activa
    )
    for env_key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
                    "KIMI_API_KEY", "ZAI_API_KEY"):
        monkeypatch.setenv(env_key, "valor-de-la-env-var")

    for provider_id in ("openai", "deepseek", "gemini", "moonshot", "zhipu"):
        with pytest.raises(CredentialUnavailableError):
            await credential_resolver.resolve_credential(provider_id)


def test_el_modulo_ya_no_expone_el_camino_de_fallback():
    assert not hasattr(credential_resolver, "_PROVIDER_ENV_KEY_MAP"), (
        "_PROVIDER_ENV_KEY_MAP era el mapa del fallback; se retiró con B1.4"
    )
    assert not hasattr(credential_resolver, "resolve_credential_instrumented"), (
        "resolve_credential_instrumented se retiró: los consumidores llaman "
        "directo a resolve_credential"
    )


def test_ningun_archivo_del_backend_nombra_al_instrumentado():
    """Guarda contra la reaparición del símbolo por copia/pega."""
    raiz = pathlib.Path(__file__).resolve().parents[1]
    r = subprocess.run(
        ["grep", "-rln", "resolve_credential_instrumented", "--include=*.py", "."],
        cwd=raiz, capture_output=True, text=True,
    )
    encontrados = [linea for linea in r.stdout.splitlines() if linea.strip()]
    # Este archivo lo nombra a propósito (en el docstring y en el control).
    encontrados = [f for f in encontrados if not f.endswith("test_retiro_fallback_env.py")]
    assert encontrados == [], f"quedan referencias al instrumentado: {encontrados}"
