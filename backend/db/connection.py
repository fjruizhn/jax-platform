import asyncio
import os
import weakref

import aiomysql

# UN POOL POR EVENT LOOP, no un global unico.
#
# POR QUE: un pool de aiomysql queda ATADO al loop que lo creo -- sus futuros
# internos (Pool._wakeup) viven en ese loop. Un global unico funciona en
# produccion, donde uvicorn corre un solo loop para siempre, pero en la suite
# hay dos: el del portal de `TestClient` (que levanta el lifespan de la app y
# ahi crea el pool) y el de pytest-asyncio. El primero en pedirlo lo creaba, y
# el otro se lo encontraba prestado:
#
#   RuntimeError: Task <...> got Future <Task pending coro=<Pool._wakeup()>>
#   attached to a different loop
#
# Eso rompia 3 tests y ademas el TEARDOWN del fixture `client`
# ("Packet sequence number wrong - got 2 expected 1", que es la misma
# corrupcion vista desde el otro lado: dos loops usando el mismo socket).
#
# El diccionario NO crece en produccion: un solo loop, una sola entrada, mismo
# comportamiento que antes byte a byte. Crece una entrada por loop en tests,
# que es exactamente lo correcto -- cada loop necesita el suyo. Es
# WeakKeyDictionary a proposito: cuando un loop de test muere, su entrada se
# va sola y no queda un pool colgado del diccionario para siempre.
_pools: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, aiomysql.Pool]" = (
    weakref.WeakKeyDictionary()
)


async def get_pool() -> aiomysql.Pool:
    loop = asyncio.get_running_loop()
    pool = _pools.get(loop)
    # `pool.closed` ademas de None: close_pool() en el lifespan de un TestClient
    # deja la entrada cerrada, y el siguiente test del mismo loop tiene que
    # recibir un pool nuevo y no uno muerto.
    if pool is None or pool.closed:
        # FAIL-CLOSED, no default silencioso. `localhost:3306` no es un default
        # razonable: esa instancia de MariaDB NO EXISTE en hall9000 (la real
        # escucha en :3308, ver memoria jax-dual-mariadb-instances), asi que el
        # default convertia "falta configuracion" en "conecta a una instancia
        # muerta". Este es el pool PRINCIPAL del backend -- lo usan 22 modulos.
        # Mismo guard que jax/core, portado a mano: repos separados, sin
        # paquete compartido (patron declarado).
        host = os.environ.get("JAX_DB_HOST")
        port = os.environ.get("JAX_DB_PORT")
        if not host or not port:
            raise RuntimeError(
                "JAX_DB_HOST/JAX_DB_PORT no están seteados -- sin default "
                "silencioso a localhost:3306 (esa instancia está muerta, ver "
                "memoria jax-dual-mariadb-instances). Sourceá /etc/jax/.env o "
                "exportalos a mano antes de conectar."
            )
        pool = await aiomysql.create_pool(
            host=host,
            port=int(port),
            user=os.getenv("JAX_DB_USER", "jax_user"),
            password=os.getenv("JAX_DB_PASSWORD", ""),
            db=os.getenv("JAX_DB_NAME", "jax_memory"),
            charset="utf8mb4",
            autocommit=True,
            minsize=1,
            maxsize=10,
        )
        _pools[loop] = pool
    return pool


async def close_pool():
    """Cierra el pool DE ESTE loop, que es el unico que este loop puede esperar.

    `wait_closed()` sobre un pool de otro loop volveria a cruzar loops, que es
    el defecto que este modulo acaba de cerrar. En produccion no hay
    diferencia: hay un solo loop y un solo pool.
    """
    loop = asyncio.get_running_loop()
    pool = _pools.pop(loop, None)
    if pool is not None:
        pool.close()
        await pool.wait_closed()
