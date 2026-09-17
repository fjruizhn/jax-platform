"""Login sin enumeración de cuentas (2026-09-12).

Hallazgo al contrastar el blueprint de Ricardo (§10, "timing uniforme") con
api/auth.py: el login dejaba saber qué cuentas existen por tres caminos, sin
necesidad de adivinar ninguna contraseña:
  - email inexistente -> 401 inmediato, sin bcrypt; email real -> bcrypt. La
    diferencia de tiempo delata la cuenta;
  - `403 Usuario inactivo` y `423 Cuenta bloqueada` salían ANTES de verificar
    la contraseña;
  - el intento fallido que activaba el bloqueo respondía
    `423 Cuenta bloqueada por 15 minutos`, aunque la contraseña fuera mala.

Regla: sin la contraseña correcta, SIEMPRE la misma respuesta (401 + detail
genérico), y un email inexistente paga un bcrypt igual que uno real. El
estado de la cuenta (inactiva, bloqueada) solo se revela a quien ya probó
ser su dueño. El contador de intentos y el bloqueo siguen funcionando.

Corre contra jax_memory_test (conftest.py). Cada test crea su usuario con un
email único y lo borra al terminar.
"""
import asyncio
import threading
import uuid
from datetime import timedelta
from tiempo import utc_ahora
from unittest.mock import patch

GENERICO = (401, "Usuario o contraseña incorrectos")
CLAVE = "clave-correcta-de-prueba"


def _email():
    return f"test-enum-{uuid.uuid4().hex[:12]}@example.invalid"


async def _crear(email, status="active", failed_attempts=0, locked_until=None):
    from db.connection import get_pool
    from db.seed import _hash
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO jax_users (tenant_id, email, password_hash, role, status, "
                "failed_attempts, locked_until) VALUES (1, %s, %s, 'operator', %s, %s, %s)",
                (email, _hash(CLAVE), status, failed_attempts, locked_until),
            )
        await conn.commit()


async def _estado(email):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT failed_attempts, locked_until FROM jax_users WHERE email = %s", (email,)
            )
            return await cur.fetchone()


async def _borrar(email):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM jax_users WHERE email = %s", (email,))
        await conn.commit()


def _login(client, email, password):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    return r.status_code, r.json().get("detail")


def test_email_inexistente_paga_un_bcrypt_como_uno_real(client):
    llamadas = []

    async def espia(plain, hashed):
        llamadas.append(hashed)
        return False

    with patch("api.auth.verify_password", espia):
        resultado = _login(client, _email(), "cualquiera")

    assert resultado == GENERICO
    assert len(llamadas) == 1, "un email inexistente no verificó contra ningún hash"
    assert llamadas[0].startswith("$2b$12$"), "el hash de relleno no cuesta lo mismo que uno real"


def test_cuenta_inactiva_sin_contrasena_correcta_no_se_revela(client):
    email = _email()
    client.portal.call(_crear, email, "inactive")
    try:
        assert _login(client, email, "mala") == GENERICO
        assert _login(client, email, CLAVE) == (403, "Usuario inactivo")
    finally:
        client.portal.call(_borrar, email)


def test_cuenta_bloqueada_sin_contrasena_correcta_no_se_revela(client):
    email = _email()
    client.portal.call(_crear, email, "active", 5, utc_ahora() + timedelta(minutes=10))
    try:
        assert _login(client, email, "mala") == GENERICO
        status, detail = _login(client, email, CLAVE)
        assert status == 423 and detail["code"] == "cuenta_bloqueada", (status, detail)
        assert 0 < detail["retry_after_seconds"] <= 600, detail
    finally:
        client.portal.call(_borrar, email)


def test_el_intento_que_bloquea_responde_generico_pero_bloquea(client):
    email = _email()
    client.portal.call(_crear, email)
    try:
        respuestas = [_login(client, email, "mala") for _ in range(5)]
        assert respuestas == [GENERICO] * 5, respuestas
        failed_attempts, locked_until = client.portal.call(_estado, email)
        assert failed_attempts == 5
        assert locked_until is not None, "el quinto intento fallido no bloqueó la cuenta"
        status, _ = _login(client, email, CLAVE)
        assert status == 423, "con el bloqueo activo, la contraseña correcta debe ver el bloqueo"
    finally:
        client.portal.call(_borrar, email)


def test_verify_password_no_bloquea_el_event_loop():
    """Un bcrypt de costo 12 son cientos de ms de CPU: dentro de una
    corrutina, congela el event loop entero (LAS CUATRO DEL RENDIMIENTO, #3).
    Con el hash de relleno, cada email inexistente también lo paga."""
    import db.seed as seed
    hilos = []
    original = seed.bcrypt.checkpw

    def espia(plain, hashed):
        hilos.append(threading.current_thread() is threading.main_thread())
        return original(plain, hashed)

    hashed = seed._hash("x")
    with patch.object(seed.bcrypt, "checkpw", espia):
        assert asyncio.run(seed.verify_password("x", hashed)) is True

    assert hilos == [False], "bcrypt corrió en el hilo del event loop"
