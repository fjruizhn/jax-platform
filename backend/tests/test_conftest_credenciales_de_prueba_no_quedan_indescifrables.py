"""Las credenciales de prueba sembradas por conftest.py decodifican de
verdad con la FERNET_KEY de esta sesión (2026-09-28, MINOR-5 de la cuarta
ronda de la auditoría adversarial).

Hallazgo real: `base_de_test.py` clona TODAS las tablas chicas de la
plantilla `jax_memory_test`, filas incluidas -- `credential` es una de
ellas, con 5 filas reales (openai/deepseek/gemini/moonshot/zhipu) cifradas
con la FERNET_KEY que estaba vigente cuando se sembró la PLANTILLA, no con
la de esta sesión (fresca y distinta por sesión desde la lista blanca de
la ronda anterior -- verificado con
`SELECT COUNT(*) FROM credential`/`axioma_config WHERE config_key='smtp.password'`
contra la plantilla real: 5 filas en `credential`, 0 en `smtp.password`).
`tests/conftest.py::_sembrar_credenciales_de_prueba` sólo miraba "¿existe
una fila activa?" -- y esas 5 SIEMPRE existen, así que nunca llegaba a
sembrar su propio "ci-dummy-not-a-real-key": cada sesión de test corría
con 5 credenciales de proveedor INDESCIFRABLES con su propia llave, en
silencio (`crypto_secrets.decrypt_secret()` es fail-soft -- ante un token
inválido devuelve el CIFRADO tal cual, nunca revienta, así que nada
avisaba). El arreglo agrega la comprobación que faltaba: si la fila activa
que ya existe no decodifica con la llave de esta sesión, se la reemplaza
(se revoca y se inserta una fresca), igual que una rotación real.

Este archivo prueba el MECANISMO (detectar "no decodifica con la llave de
esta sesión" y reemplazar) contra la base real de la sesión, con
criptografía real -- NO importa `tests.conftest` para invocar la función
privada de nuevo: un `import tests.conftest` (module-level o perezoso,
misma cuenta) desde un archivo de test puede registrarse bajo una entrada
de `sys.modules` DISTINTA a la que usó el autodescubrimiento de conftest de
pytest, y volver a ejecutar TODO el código de nivel de módulo de
conftest.py -- hallazgo real y medido en esta misma sesión (ronda anterior,
ver el comentario grande en
test_conftest_no_carga_secretos_no_necesarios.py): 48 tests de
test_kill_switch*.py/test_carril_mesa.py/test_facet_canary_freno.py se
pusieron en rojo la primera vez que esto pasó. La corrección de
conftest.py se revisa por lectura del diff, no reinvocándola acá."""
import pytest

from crypto_secrets import decrypt_db_secret, encrypt_secret


async def _pool():
    from db.connection import get_pool
    return await get_pool()


async def _fila_activa(provider_id):
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, encrypted_value FROM credential "
                "WHERE provider_id=%s AND state='active' LIMIT 1",
                (provider_id,),
            )
            return await cur.fetchone()


async def _corromper_credencial_activa(provider_id, env_key):
    """Simula EXACTAMENTE lo que deja la plantilla: una fila 'active' cuyo
    `encrypted_value` NO decodifica con la FERNET_KEY de esta sesión
    (cifrada acá con una llave fabricada -- no hace falta la real de
    producción para reproducir "no decodifica con la llave de esta
    sesión", sólo una llave DISTINTA)."""
    from cryptography.fernet import Fernet
    llave_ajena = Fernet.generate_key()
    cifrado_con_llave_ajena = Fernet(llave_ajena).encrypt(b"valor-de-otra-epoca").decode()

    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE credential SET state='revoked', revoked_at=NOW() "
                "WHERE provider_id=%s AND state='active'", (provider_id,))
            await cur.execute(
                "INSERT INTO credential (provider_id, env_key, encrypted_value, state, activated_at) "
                "VALUES (%s, %s, %s, 'active', NOW())",
                (provider_id, env_key, cifrado_con_llave_ajena),
            )
        await conn.commit()


async def _reemplazar_si_no_decodifica(provider_id, env_key, cifrada_con_la_llave_de_la_sesion):
    """El MISMO mecanismo que `tests/conftest.py::_sembrar_credenciales_de_prueba`
    aplica ahora (ver su comentario, MINOR-5): revisa si la fila activa
    decodifica con la llave de ESTA sesión; si no, la revoca y siembra una
    fresca. Reimplementado acá A PROPÓSITO (no importado) -- ver el
    docstring del módulo sobre por qué no se reimporta `tests.conftest`."""
    pool = await _pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, encrypted_value FROM credential "
                "WHERE provider_id=%s AND state='active' LIMIT 1",
                (provider_id,),
            )
            fila = await cur.fetchone()
            if fila is not None:
                credencial_id, valor_cifrado = fila
                if decrypt_db_secret(valor_cifrado):
                    return  # ya decodifica con la llave de esta sesión -- no se toca
                await cur.execute(
                    "UPDATE credential SET state='revoked', revoked_at=NOW() WHERE id=%s",
                    (credencial_id,),
                )
            await cur.execute(
                "INSERT INTO credential (provider_id, env_key, encrypted_value, state, activated_at) "
                "VALUES (%s, %s, %s, 'active', NOW())",
                (provider_id, env_key, cifrada_con_la_llave_de_la_sesion),
            )
        await conn.commit()


@pytest.fixture
def restaurar_credencial_openai(client):
    """El sembrado real ya corrió al levantar `client` (fixture de sesión) --
    este test la corrompe a propósito; al terminar, la vuelve a reemplazar
    para dejarla utilizable para el resto de la sesión (mismo criterio que
    `limpiar_catalogo_sync_ejecucion`: no asumir que el test siguiente no la
    necesita sana)."""
    yield
    cifrada = encrypt_secret("ci-dummy-not-a-real-key")
    client.portal.call(_reemplazar_si_no_decodifica, "openai", "OPENAI_API_KEY", cifrada)


def test_una_fila_cifrada_con_otra_llave_no_decodifica_con_decrypt_db_secret(
        client, restaurar_credencial_openai):
    """La sonda del defecto en sí: reproduce, con criptografía Fernet REAL
    (no mockeada), que una fila 'active' heredada con OTRA llave no
    decodifica -- exactamente lo que dejaba la plantilla `jax_memory_test`
    en cada sesión antes de este arreglo."""
    client.portal.call(_corromper_credencial_activa, "openai", "OPENAI_API_KEY")

    _id_corrupta, valor_corrupto = client.portal.call(_fila_activa, "openai")
    assert decrypt_db_secret(valor_corrupto) == ""


def test_el_mecanismo_de_reemplazo_deja_la_fila_utilizable_con_la_llave_de_la_sesion(
        client, restaurar_credencial_openai):
    client.portal.call(_corromper_credencial_activa, "openai", "OPENAI_API_KEY")
    id_corrupta, _valor = client.portal.call(_fila_activa, "openai")

    cifrada = encrypt_secret("ci-dummy-not-a-real-key")
    client.portal.call(_reemplazar_si_no_decodifica, "openai", "OPENAI_API_KEY", cifrada)

    id_nueva, valor_nuevo = client.portal.call(_fila_activa, "openai")
    assert id_nueva != id_corrupta, "no reemplazó la fila -- sigue siendo la misma"
    assert decrypt_db_secret(valor_nuevo) == "ci-dummy-not-a-real-key"


def test_el_mecanismo_de_reemplazo_no_toca_una_fila_que_ya_decodifica(client):
    """Control: si la fila activa YA decodifica con la llave de esta
    sesión (el caso normal, sin la plantilla de por medio), no se la
    reemplaza -- nunca hace falta, y reemplazarla sin necesidad rotaría el
    `id` en cada llamada."""
    id_antes, _valor = client.portal.call(_fila_activa, "openai")

    cifrada = encrypt_secret("ci-dummy-not-a-real-key")
    client.portal.call(_reemplazar_si_no_decodifica, "openai", "OPENAI_API_KEY", cifrada)

    id_despues, valor_despues = client.portal.call(_fila_activa, "openai")
    assert id_despues == id_antes
    assert decrypt_db_secret(valor_despues) == "ci-dummy-not-a-real-key"
