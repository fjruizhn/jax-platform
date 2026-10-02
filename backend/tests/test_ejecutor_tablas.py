# backend/tests/test_ejecutor_tablas.py
"""Tablas del Ejecutor (SP1, plan 1): prohibiciones, inventario y puntos de
restauración. Contra jax_memory_test. La semilla de reglas corre UNA vez: una
regla que el admin desactiva después no revive en el próximo arranque."""
import json

import pytest

from db.migrations import (
    MIGRACION_EJECUTOR_INVENTARIO_V1, MIGRACION_EJECUTOR_REGLAS_ENVOLTORIOS_V1, MIGRACION_EJECUTOR_REGLAS_V1,
    _asegurar_forma_de_ejecutor_punto_restauracion, _ejecutor_inventario_v1,
    _ejecutor_reglas_envoltorios_v1, _ejecutor_reglas_v1, _METODOS_PUNTO_RESTAURACION,
    parsear_inventario,
)
from tests.identidades import sql

_SEMILLA_CODIGOS = {
    "canario_c1", "ssh_sin_tt", "apt_full_upgrade_bridge", "migrate_fresh_produccion", "sed_i_env",
    "pure_ftpd_parar_atemai", "respaldos_borrar", "ajustes_claude_code", "borrar_archivos",
    "sql_destructivo", "dns_correo", "parar_servicio", "quitar_paquetes", "disco",
}
_ENVOLTORIOS_CODIGOS = {
    "envoltorio_tmux_screen", "envoltorio_desacopla", "envoltorio_script_c", "envoltorio_at_batch",
    "envoltorio_systemd_run", "envoltorio_ssh_escondido", "sandbox_desactivado",
}


async def _correr(funcion):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await funcion(cur)
        await conn.commit()


def _vaciar(client):
    for nombre in (MIGRACION_EJECUTOR_REGLAS_V1, MIGRACION_EJECUTOR_REGLAS_ENVOLTORIOS_V1,
                   MIGRACION_EJECUTOR_INVENTARIO_V1):
        client.portal.call(sql, "DELETE FROM axioma_migracion_de_datos WHERE nombre = %s", (nombre,))
    client.portal.call(sql, "DELETE FROM ejecutor_punto_restauracion")
    client.portal.call(sql, "DELETE FROM ejecutor_regla")
    client.portal.call(sql, "DELETE FROM ejecutor_host")


@pytest.fixture
def sin_marcas(client):
    """jax_memory_test es compartida entre frentes: se deja como se encontró el
    esquema (vacío de filas del Ejecutor) y la semilla vuelve a correr en el
    próximo arranque de la suite."""
    _vaciar(client)
    yield
    _vaciar(client)


def test_la_semilla_trae_las_catorce_reglas_con_un_solo_canario(client, sin_marcas):
    client.portal.call(_correr, _ejecutor_reglas_v1)
    filas = client.portal.call(sql, "SELECT codigo, es_canario, ejemplos_coincide FROM ejecutor_regla", None, True)
    assert {f[0] for f in filas} == _SEMILLA_CODIGOS
    assert sum(1 for f in filas if f[1]) == 1
    assert all(json.loads(f[2]) for f in filas), "una regla sin ejemplo que coincida no se vio bloquear"


def test_la_semilla_corre_una_sola_vez(client, sin_marcas):
    client.portal.call(_correr, _ejecutor_reglas_v1)
    client.portal.call(sql, "UPDATE ejecutor_regla SET activa = 0 WHERE codigo = 'sed_i_env'")
    client.portal.call(_correr, _ejecutor_reglas_v1)
    assert client.portal.call(sql, "SELECT activa FROM ejecutor_regla WHERE codigo = 'sed_i_env'", None, True) == ((0,),)


def test_los_envoltorios_se_suman_a_la_semilla_una_sola_vez(client, sin_marcas):
    client.portal.call(_correr, _ejecutor_reglas_v1)
    client.portal.call(_correr, _ejecutor_reglas_envoltorios_v1)
    filas = client.portal.call(sql, "SELECT codigo, tipo, es_canario, activa FROM ejecutor_regla", None, True)
    assert {f[0] for f in filas} == _SEMILLA_CODIGOS | _ENVOLTORIOS_CODIGOS
    assert sum(1 for f in filas if f[2]) == 1
    assert all(f[1] == "prohibido" and f[3] == 1 for f in filas if f[0] in _ENVOLTORIOS_CODIGOS)
    client.portal.call(sql, "UPDATE ejecutor_regla SET activa = 0 WHERE codigo = 'envoltorio_at_batch'")
    client.portal.call(_correr, _ejecutor_reglas_envoltorios_v1)
    assert client.portal.call(sql, "SELECT activa FROM ejecutor_regla WHERE codigo = 'envoltorio_at_batch'",
                              None, True) == ((0,),)
    assert client.portal.call(sql, "SELECT COUNT(*) FROM axioma_migracion_de_datos WHERE nombre = %s",
                              (MIGRACION_EJECUTOR_REGLAS_ENVOLTORIOS_V1,), True) == ((1,),)


def test_los_envoltorios_llegan_aunque_la_semilla_v1_ya_estuviera(client, sin_marcas):
    """Producción: v1 corrió el 2026-09-17 04:4x; la migración nueva tiene que entrar sola."""
    client.portal.call(_correr, _ejecutor_reglas_v1)
    antes = client.portal.call(sql, "SELECT COUNT(*) FROM ejecutor_regla", None, True)[0][0]
    client.portal.call(_correr, _ejecutor_reglas_envoltorios_v1)
    assert client.portal.call(sql, "SELECT COUNT(*) FROM ejecutor_regla", None, True)[0][0] == antes + 7


def test_la_edad_maxima_de_c2_se_siembra_sin_pisar(client, sin_marcas):
    client.portal.call(sql, "DELETE FROM axioma_config WHERE config_key = 'ejecutor.c2_edad_max_s'")
    client.portal.call(_correr, _ejecutor_reglas_v1)
    assert client.portal.call(sql, "SELECT config_value FROM axioma_config WHERE config_key = 'ejecutor.c2_edad_max_s'",
                              None, True) == (("86400",),)


def test_inventario_desde_el_entorno(client, sin_marcas, monkeypatch):
    monkeypatch.setenv("JAX_EJECUTOR_INVENTARIO",
                       "hall9000:192.0.2.5:58291:hypervisor:local+sin_clientes,bridge:192.0.2.20:58291:clientes")
    client.portal.call(_correr, _ejecutor_inventario_v1)
    filas = client.portal.call(sql, "SELECT nombre, ip, puerto, rol, es_local, con_datos_de_clientes FROM ejecutor_host "
                                    "ORDER BY nombre", None, True)
    assert filas == (("bridge", "192.0.2.20", 58291, "clientes", 0, 1),
                     ("hall9000", "192.0.2.5", 58291, "hypervisor", 1, 0))


def test_inventario_ausente_no_marca_la_migracion(client, sin_marcas, monkeypatch):
    monkeypatch.delenv("JAX_EJECUTOR_INVENTARIO", raising=False)
    client.portal.call(_correr, _ejecutor_inventario_v1)
    assert client.portal.call(sql, "SELECT COUNT(*) FROM axioma_migracion_de_datos WHERE nombre = %s",
                              (MIGRACION_EJECUTOR_INVENTARIO_V1,), True) == ((0,),)


def test_inventario_mal_formado_no_tumba_ni_marca(client, sin_marcas, monkeypatch):
    monkeypatch.setenv("JAX_EJECUTOR_INVENTARIO", "hall9000:192.0.2.5:no-es-puerto:hypervisor")
    client.portal.call(_correr, _ejecutor_inventario_v1)
    assert client.portal.call(sql, "SELECT COUNT(*) FROM ejecutor_host", None, True) == ((0,),)


@pytest.mark.parametrize("texto", [
    "", "a:1.2.3.4:22", "a:1.2.3.4:22:rol_raro", "a:1.2.3.4:0:clientes", "a:1.2.3.4:22:clientes:opcion_rara",
    "a:1.2.3.4:22:clientes,a:1.2.3.5:22:clientes",
])
def test_parsear_inventario_rechaza(texto):
    with pytest.raises(ValueError):
        parsear_inventario(texto)


async def _sembrar_host_de_prueba(cur_unused=None):
    """Un host propio para estos dos tests, para que la FK de host_nombre nunca sea la razón
    de un fallo: lo que se mide es metodo/respaldado_at, no la FK."""
    await sql(
        "INSERT IGNORE INTO ejecutor_host (nombre, ip, puerto, rol) "
        "VALUES ('zz_test_forma_prc', '192.0.2.77', 22, 'clientes')"
    )


def test_metodo_desconocido_no_se_guarda(client, sin_marcas):
    """C2 (Esquema, diseño 2026-09-22): `metodo` es ENUM de los 4 métodos reales -- un valor
    fuera de la lista no se guarda, ni truncado ni silencioso."""
    import pymysql

    async def _intentar():
        await _sembrar_host_de_prueba()
        try:
            await sql(
                "INSERT INTO ejecutor_punto_restauracion (host_nombre, referencia, metodo, "
                "respaldado_at, restaurado_y_verificado_at, verificado_por, evidencia) "
                "VALUES ('zz_test_forma_prc', %s, 'metodo_inventado', UTC_TIMESTAMP(), UTC_TIMESTAMP(), 'test', 'test')",
                ("referencia-1",),
            )
            return None
        except pymysql.err.DataError as exc:
            return exc.args

    error = client.portal.call(_intentar)
    assert error is not None, "un metodo fuera del ENUM se guardó -- no debería"
    filas = client.portal.call(sql, "SELECT COUNT(*) FROM ejecutor_punto_restauracion "
                                    "WHERE metodo = 'metodo_inventado'", None, True)
    assert filas == ((0,),)


def test_respaldado_at_es_obligatorio(client, sin_marcas):
    """respaldado_at es la hora del SNAPSHOT (distinta de restaurado_y_verificado_at) --
    sin ella, la fila no se guarda."""
    import pymysql

    async def _intentar():
        await _sembrar_host_de_prueba()
        try:
            await sql(
                "INSERT INTO ejecutor_punto_restauracion (host_nombre, referencia, metodo, "
                "restaurado_y_verificado_at, verificado_por, evidencia) "
                "VALUES ('zz_test_forma_prc', %s, 'recreacion', UTC_TIMESTAMP(), 'test', 'test')",
                ("referencia-2",),
            )
            return None
        except (pymysql.err.OperationalError, pymysql.err.IntegrityError) as exc:
            return exc.args

    error = client.portal.call(_intentar)
    assert error is not None, "una fila sin respaldado_at se guardó -- no debería"


def test_la_consulta_del_exportador_usa_el_indice(client, sin_marcas, monkeypatch):
    monkeypatch.setenv("JAX_EJECUTOR_INVENTARIO", "a:192.0.2.1:22:clientes,b:192.0.2.2:22:clientes")
    client.portal.call(_correr, _ejecutor_inventario_v1)
    for k in range(200):
        client.portal.call(sql, "INSERT INTO ejecutor_punto_restauracion (host_nombre, referencia, metodo, "
                                "respaldado_at, restaurado_y_verificado_at, verificado_por, evidencia) "
                                "VALUES (%s, %s, 'recreacion', UTC_TIMESTAMP(), UTC_TIMESTAMP(), 'test', 'test')",
                                ("ab"[k % 2], f"prueba-{k}"))
    filas = client.portal.call(sql, "EXPLAIN SELECT host_nombre, MAX(restaurado_y_verificado_at) "
                                    "FROM ejecutor_punto_restauracion GROUP BY host_nombre", None, True)
    assert any("idx_ejecutor_punto_host_fecha" in str(f) for f in filas), filas


def test_la_edad_del_respaldo_usa_su_propio_indice(client, sin_marcas, monkeypatch):
    """C2 (Esquema, diseño 2026-09-22): la edad del PUNTO DE RESTAURACIÓN se mide sobre
    `respaldado_at` (la hora del snapshot), no sobre `restaurado_y_verificado_at` -- son
    preguntas distintas y cada una tiene su índice. `idx_ejecutor_punto_host_fecha` (arriba)
    no cubre esta consulta: MariaDB no puede usar un índice que empieza por
    (host_nombre, restaurado_y_verificado_at) para ordenar/agrupar por respaldado_at."""
    monkeypatch.setenv("JAX_EJECUTOR_INVENTARIO", "a:192.0.2.1:22:clientes,b:192.0.2.2:22:clientes")
    client.portal.call(_correr, _ejecutor_inventario_v1)
    for k in range(200):
        client.portal.call(sql, "INSERT INTO ejecutor_punto_restauracion (host_nombre, referencia, metodo, "
                                "respaldado_at, restaurado_y_verificado_at, verificado_por, evidencia) "
                                "VALUES (%s, %s, 'recreacion', UTC_TIMESTAMP(), UTC_TIMESTAMP(), 'test', 'test')",
                                ("ab"[k % 2], f"prueba-{k}"))
    filas = client.portal.call(sql, "EXPLAIN SELECT host_nombre, MAX(respaldado_at) "
                                    "FROM ejecutor_punto_restauracion GROUP BY host_nombre", None, True)
    assert any("idx_ejecutor_punto_host_respaldo" in str(f) for f in filas), filas


def test_ya_es_enum_exige_el_conjunto_exacto(client, sin_marcas):
    """Hallazgo de la auditoría adversarial (2026-09-22): `ya_es_enum` comparaba "¿están los 4
    valores?", no "¿son EXACTAMENTE estos 4?" -- un ENUM con un quinto valor de más pasaba el
    chequeo viejo y el MODIFY que lo recorta nunca corría. Se siembra a mano un ENUM con un
    valor extra y se comprueba que la función lo detecta y lo recorta."""
    async def _romper_y_reparar():
        enum_con_extra = ",".join(f"'{m}'" for m in (*_METODOS_PUNTO_RESTAURACION, "algo_extra"))
        await sql(f"ALTER TABLE ejecutor_punto_restauracion MODIFY COLUMN metodo ENUM({enum_con_extra}) NOT NULL")
        antes = await sql(
            "SELECT COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME='ejecutor_punto_restauracion' AND COLUMN_NAME='metodo'", None, True)
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await _asegurar_forma_de_ejecutor_punto_restauracion(cur)
            await conn.commit()
        despues = await sql(
            "SELECT COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME='ejecutor_punto_restauracion' AND COLUMN_NAME='metodo'", None, True)
        return antes, despues

    antes, despues = client.portal.call(_romper_y_reparar)
    assert "algo_extra" in antes[0][0]
    assert "algo_extra" not in despues[0][0], despues


def test_ya_es_enum_exige_not_null(client, sin_marcas):
    """Mutante B de la re-auditoría (2026-09-22): un ENUM con el conjunto EXACTO de valores
    pero declarado NULL también tiene que detectarse como "todavía no es la forma final" --
    si no, el MODIFY COLUMN ... NOT NULL de abajo nunca corre y la columna admite NULL para
    siempre."""
    async def _romper_y_reparar():
        enum_exacto = ",".join(f"'{m}'" for m in _METODOS_PUNTO_RESTAURACION)
        await sql(f"ALTER TABLE ejecutor_punto_restauracion MODIFY COLUMN metodo ENUM({enum_exacto}) NULL")
        antes = await sql(
            "SELECT IS_NULLABLE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME='ejecutor_punto_restauracion' AND COLUMN_NAME='metodo'", None, True)
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await _asegurar_forma_de_ejecutor_punto_restauracion(cur)
            await conn.commit()
        despues = await sql(
            "SELECT IS_NULLABLE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME='ejecutor_punto_restauracion' AND COLUMN_NAME='metodo'", None, True)
        return antes, despues

    antes, despues = client.portal.call(_romper_y_reparar)
    assert antes == (("YES",),), antes
    assert despues == (("NO",),), despues


def test_sudo_y_machine_id_se_eliminan_de_ejecutor_host(client):
    """Ronda 3 (decisión de Hyde, 2026-09-22): ningún lector ni escritor toca estas dos
    columnas en jax ni en jax-platform (confirmado con grep, dos veces); la fuente real de
    sudo/machine-id es jax/scripts/ejecutor_fase0/maquinas.toml. DROP idempotente."""
    from db.migrations import _eliminar_sudo_y_machine_id_de_ejecutor_host

    async def _sembrar_columnas_viejas_y_eliminar():
        for columna, ddl in (
            ("sudo", "ALTER TABLE ejecutor_host ADD COLUMN sudo BOOLEAN NOT NULL DEFAULT FALSE"),
            ("machine_id", "ALTER TABLE ejecutor_host ADD COLUMN machine_id CHAR(32) NULL"),
        ):
            existe = await sql(
                "SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                "AND TABLE_NAME='ejecutor_host' AND COLUMN_NAME=%s", (columna,), True)
            if not existe:
                await sql(ddl)
        antes = await sql(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME='ejecutor_host' AND COLUMN_NAME IN ('sudo','machine_id')", None, True)

        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await _eliminar_sudo_y_machine_id_de_ejecutor_host(cur)
            await conn.commit()

        despues = await sql(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME='ejecutor_host' AND COLUMN_NAME IN ('sudo','machine_id')", None, True)
        return antes, despues

    antes, despues = client.portal.call(_sembrar_columnas_viejas_y_eliminar)
    assert {f[0] for f in antes} == {"sudo", "machine_id"}, antes
    assert despues == (), despues


def test_ejecutor_host_nace_sin_sudo_ni_machine_id(client):
    """Una instalación NUEVA (CREATE_EJECUTOR_HOST) no las trae -- no hay motivo para crear y
    después dropear en la misma corrida."""
    columnas = client.portal.call(
        sql, "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME='ejecutor_host'", None, True)
    nombres = {f[0] for f in columnas}
    assert "sudo" not in nombres, nombres
    assert "machine_id" not in nombres, nombres


def test_ejecutor_repo_y_columnas_de_codigo(client):
    """Task 8 (plan "El Ejecutor programa"): `ejecutor_repo` (el inventario de repos que el
    Ejecutor puede clonar) y las columnas de misión de código en `ejecutor_mision`."""
    columnas_repo = client.portal.call(sql, "SHOW COLUMNS FROM ejecutor_repo", None, True)
    assert {r[0] for r in columnas_repo} >= {"id", "owner_repo", "remoto_url", "comandos_prueba", "activo"}
    columnas_mision = client.portal.call(sql, "SHOW COLUMNS FROM ejecutor_mision", None, True)
    assert {r[0] for r in columnas_mision} >= {"tipo", "repo_id", "rama", "pr_url", "estado_entrega"}


def test_comandos_prueba_debe_ser_array(client):
    """Ronda de revisión de Task 8: el CHECK (JSON_TYPE(comandos_prueba) = 'ARRAY') de
    ejecutor_repo -- un JSON válido que NO es array (un objeto, acá) se rechaza. Insertar
    con un owner_repo único (uuid) para no chocar con la fila sembrada de jax-platform ni
    con otra corrida en paralelo sobre la misma jax_memory_test compartida."""
    import uuid

    import pymysql

    async def _intentar():
        try:
            await sql(
                "INSERT INTO ejecutor_repo (owner_repo, remoto_url, comandos_prueba) VALUES (%s, %s, %s)",
                (f"zz-test/check-{uuid.uuid4()}", "https://example.invalid/x.git",
                 json.dumps({"no": "es un array"})))
            return None
        except pymysql.err.OperationalError as exc:
            return exc.args

    error = client.portal.call(_intentar)
    assert error is not None, "un comandos_prueba que no es array se guardó -- no debería"
    assert error[0] == 4025, error  # CONSTRAINT ... failed


def test_tipo_fuera_del_enum_no_se_guarda(client):
    """Ronda de revisión de Task 8: ejecutor_mision.tipo es ENUM('servidor','codigo') -- un
    valor fuera de esos dos se rechaza, ni truncado ni silencioso (mismo criterio que
    test_metodo_desconocido_no_se_guarda para ejecutor_punto_restauracion.metodo)."""
    import uuid

    import pymysql

    async def _intentar():
        mision_id = str(uuid.uuid4())
        try:
            await sql(
                "INSERT INTO ejecutor_mision (id, user_id, objetivo, maquinas, sesion_id, created_at, "
                "updated_at, tipo) VALUES (%s, 1, 'x', '[]', %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6), %s)",
                (mision_id, str(uuid.uuid4()), "tipo_inventado"))
            return None
        except pymysql.err.DataError as exc:
            return exc.args

    error = client.portal.call(_intentar)
    assert error is not None, "un tipo fuera del ENUM se guardó -- no debería"
    filas = client.portal.call(sql, "SELECT COUNT(*) FROM ejecutor_mision WHERE tipo = 'tipo_inventado'",
                               None, True)
    assert filas == ((0,),)


def test_estado_entrega_fuera_del_enum_no_se_guarda(client):
    """Ronda de revisión de Task 8: ejecutor_mision.estado_entrega es
    ENUM('abierto','rechazada_por_contrato','sin_informe_c5','fallo_entrega','sin_cambios')
    -- un valor fuera de esos cinco se rechaza."""
    import uuid

    import pymysql

    async def _intentar():
        mision_id = str(uuid.uuid4())
        try:
            await sql(
                "INSERT INTO ejecutor_mision (id, user_id, objetivo, maquinas, sesion_id, created_at, "
                "updated_at, estado_entrega) VALUES "
                "(%s, 1, 'x', '[]', %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6), %s)",
                (mision_id, str(uuid.uuid4()), "estado_inventado"))
            return None
        except pymysql.err.DataError as exc:
            return exc.args

    error = client.portal.call(_intentar)
    assert error is not None, "un estado_entrega fuera del ENUM se guardó -- no debería"
    filas = client.portal.call(
        sql, "SELECT COUNT(*) FROM ejecutor_mision WHERE estado_entrega = 'estado_inventado'", None, True)
    assert filas == ((0,),)


def test_snapshot_lv_es_un_metodo_del_contrato():
    """Pre-requisito 2 del veredicto de ronda 3 (fase 4b): las VMs del ensayo viven en LVs thin
    y su punto de restauración es un snapshot de LV. `imagen_vm` y `recreacion` no sirven
    (mentirían sobre cómo se verificó). Puro, sin DB."""
    assert "snapshot_lv" in _METODOS_PUNTO_RESTAURACION
    assert len(set(_METODOS_PUNTO_RESTAURACION)) == len(_METODOS_PUNTO_RESTAURACION)


def test_la_migracion_amplia_el_enum_a_snapshot_lv_sin_perder_filas(client, sin_marcas):
    """Producción tiene el ENUM de 4 valores. La migración lo amplía a 5 SIN perder filas
    existentes (con filas, el chequeo previo de valores inválidos tiene que seguir andando con
    5 métodos), es idempotente, y después `snapshot_lv` se guarda."""
    antiguos = ("imagen_vm", "restic_ficheros", "volcado_mariadb", "recreacion")

    async def _escenario():
        await _sembrar_host_de_prueba()
        enum_viejo = ",".join(f"'{m}'" for m in antiguos)
        await sql(f"ALTER TABLE ejecutor_punto_restauracion MODIFY COLUMN metodo ENUM({enum_viejo}) NOT NULL")
        await sql(
            "INSERT INTO ejecutor_punto_restauracion (host_nombre, referencia, metodo, "
            "respaldado_at, restaurado_y_verificado_at, verificado_por, evidencia) "
            "VALUES ('zz_test_forma_prc', 'fila-previa', 'imagen_vm', UTC_TIMESTAMP(), UTC_TIMESTAMP(), 't', 't')")
        from db.connection import get_pool
        pool = await get_pool()
        for _ in range(2):  # dos veces: idempotente
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await _asegurar_forma_de_ejecutor_punto_restauracion(cur)
                await conn.commit()
        columna = await sql(
            "SELECT COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME='ejecutor_punto_restauracion' AND COLUMN_NAME='metodo'", None, True)
        await sql(
            "INSERT INTO ejecutor_punto_restauracion (host_nombre, referencia, metodo, "
            "respaldado_at, restaurado_y_verificado_at, verificado_por, evidencia) "
            "VALUES ('zz_test_forma_prc', 'vg/snap', 'snapshot_lv', UTC_TIMESTAMP(), UTC_TIMESTAMP(), 't', 't')")
        filas = await sql("SELECT referencia, metodo FROM ejecutor_punto_restauracion ORDER BY referencia", None, True)
        return columna, filas

    columna, filas = client.portal.call(_escenario)
    assert "'snapshot_lv'" in columna[0][0] and columna[0][1] == "NO", columna
    assert filas == (("fila-previa", "imagen_vm"), ("vg/snap", "snapshot_lv")), filas
