"""`backend/b9_pool.py` (PR-P1, 2026-09-25): la extracción de `_B9MappingPool`
fuera de `api/chat.py` no puede cambiar su comportamiento -- sólo su
dirección. `tests/test_chat_b9_memory_boundary.py` ya ejercita el
comportamiento end-to-end contra `api.chat`; esto fija la forma del módulo
nuevo en sí y que los dos consumidores (chat y, más adelante,
`backend/proyectos/autoridad.py`) importan la MISMA clase."""
import aiomysql

from b9_pool import _B9MappingAcquire, _B9MappingConnection, _B9MappingPool


class _CursorDeMentira:
    def __init__(self, tipo_pedido):
        self.tipo_pedido = tipo_pedido


class _ConexionDeMentira:
    def cursor(self, tipo=None):
        return _CursorDeMentira(tipo)


class _AcquireDeMentira:
    def __init__(self, conexion):
        self._conexion = conexion
        self.entrado = False
        self.salido = False

    async def __aenter__(self):
        self.entrado = True
        return self._conexion

    async def __aexit__(self, *exc):
        self.salido = True
        return False


class _PoolDeMentira:
    def __init__(self, conexion):
        self._conexion = conexion
        self.acquire_llamado = False

    def acquire(self):
        self.acquire_llamado = True
        return _AcquireDeMentira(self._conexion)


async def test_el_cursor_es_siempre_dictcursor():
    conexion = _ConexionDeMentira()
    pool = _B9MappingPool(_PoolDeMentira(conexion))
    async with pool.acquire() as conn:
        cursor = conn.cursor()
    assert isinstance(cursor, _CursorDeMentira)
    assert cursor.tipo_pedido is aiomysql.DictCursor


async def test_el_llamador_no_puede_pedir_otro_cursor():
    conexion = _ConexionDeMentira()
    conn = _B9MappingConnection(conexion)
    try:
        conn.cursor(aiomysql.SSCursor)
    except TypeError as exc:
        assert "no accepta" not in str(exc)  # sanity: el mensaje real está en inglés
        return
    raise AssertionError("se esperaba TypeError al pedir un cursor propio")


async def test_getattr_delega_a_la_conexion_real():
    class _ConexionConAtributo:
        def cursor(self, tipo=None):
            return None

        propio = "valor"

    conn = _B9MappingConnection(_ConexionConAtributo())
    assert conn.propio == "valor"


async def test_acquire_entra_y_sale_del_context_manager_subyacente():
    conexion = _ConexionDeMentira()
    pool_de_mentira = _PoolDeMentira(conexion)
    pool = _B9MappingPool(pool_de_mentira)
    acquire = pool.acquire()
    assert isinstance(acquire, _B9MappingAcquire)
    async with acquire as conn:
        assert isinstance(conn, _B9MappingConnection)
    assert pool_de_mentira.acquire_llamado


async def test_chat_importa_la_misma_clase_del_modulo_compartido():
    """`api.chat` ya no define su propia copia: importa la de `b9_pool`."""
    import api.chat as chat
    import b9_pool

    assert chat._B9MappingPool is b9_pool._B9MappingPool
