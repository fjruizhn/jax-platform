# backend/tests/test_jacobs_tope_devoluciones.py
"""El tope de devoluciones del árbitro, sembrado en axioma_config.

**Por qué existe esta semilla.** `store.get_tope_devoluciones()` (repo jax) es
fail-closed: sin la fila, o con un valor que no sea un entero >= 0, el tope es
CERO. Y cero NO significa "el árbitro está apagado": significa que la PRIMERA
objeción cae de inmediato en la rama `RESULTADO_TOPE` de `jacobs/devolucion.py`
y el pipeline termina en `disputed` sin que el trabajo se rehaga nunca. Medido
el 2026-09-20 contra la base de producción: no existía NINGUNA clave `jacobs.*`
en `axioma_config`, con el árbitro ya vivo en master desde el 18-09.

**De dónde sale el 2.** Del spec `2026-09-18-arbitro-devuelve-design.md` §3.3,
literal: «Dos devoluciones por pipeline. A la tercera, el pipeline para y avisa,
entregando las dos versiones para que Fernando decida.» No es un número elegido
acá. El riesgo de gasto que justificaría un tope más bajo ya está cerrado por
§3.4: la devolución cabe dentro del `costo_max_aceptado_usd` que el humano ya
aceptó, o no ocurre.

**Por qué acá y no a mano.** `config_audit.escribir` es el único escritor de
`axioma_config` fuera de las semillas de arranque, y el detector
`test_ningun_otro_modulo_escribe_axioma_config` lo hace cumplir. Un INSERT a
mano en producción no tiene quién lo audite ni sobrevive a una base nueva.
"""
from db.migrations import _jacobs_tope_devoluciones_v1
from tests.identidades import sql

CLAVE = "jacobs.tope_devoluciones"
VALOR = "2"


async def _correr():
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await _jacobs_tope_devoluciones_v1(cur)
        await conn.commit()


def _valor(client):
    filas = client.portal.call(sql, "SELECT config_value FROM axioma_config WHERE config_key = %s",
                               (CLAVE,), True)
    return filas[0][0] if filas else None


def test_siembra_el_tope_y_no_pisa_lo_que_puso_el_admin(client):
    client.portal.call(sql, "DELETE FROM axioma_config WHERE config_key = %s", (CLAVE,))
    assert _valor(client) is None

    client.portal.call(_correr)
    assert _valor(client) == VALOR

    # INSERT IGNORE: el arranque siguiente no pisa el cambio del admin.
    client.portal.call(sql, "UPDATE axioma_config SET config_value = '5' WHERE config_key = %s", (CLAVE,))
    client.portal.call(_correr)
    assert _valor(client) == "5"

    client.portal.call(sql, "UPDATE axioma_config SET config_value = %s WHERE config_key = %s", (VALOR, CLAVE))


def test_el_valor_sembrado_es_el_del_spec():
    """Spec 2026-09-18-arbitro-devuelve-design.md §3.3: dos devoluciones por pipeline."""
    from db.migrations import VALOR_INICIAL_TOPE_DEVOLUCIONES
    assert VALOR_INICIAL_TOPE_DEVOLUCIONES == "2"


def test_el_tope_no_nace_en_cero():
    """Cero no es un default neutro: con cero, la PRIMERA objeción del árbitro
    termina el pipeline en `disputed` sin rehacer nada. Sembrar '0' seria dejar
    el sistema exactamente como estaba y creer que se arreglo algo."""
    from db.migrations import VALOR_INICIAL_TOPE_DEVOLUCIONES
    assert int(VALOR_INICIAL_TOPE_DEVOLUCIONES) > 0


def test_la_clave_lleva_el_prefijo_del_orquestador_no_el_del_ejecutor():
    """`jacobs.` y no `ejecutor.`: es cuántas veces puede devolver el árbitro de
    Jacobs, no una clave del Ejecutor de Contratos. El nombre lo fija
    `jacobs/store.py::_CONFIG_KEY_TOPE_DEVOLUCIONES` en el repo jax; si acá se
    escribiera con otro prefijo, la semilla no la leeria nadie."""
    from db.migrations import CLAVE_TOPE_DEVOLUCIONES
    assert CLAVE_TOPE_DEVOLUCIONES == "jacobs.tope_devoluciones"
    assert not CLAVE_TOPE_DEVOLUCIONES.startswith("ejecutor.")
