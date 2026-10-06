import asyncio

import api.admin.models as admin_models


def test_aprobacion_copia_provider_y_model_id_desde_model_ref():
    class Cursor:
        def __init__(self):
            self.sql = None
            self.parameters = None

        async def execute(self, sql, parameters):
            self.sql = sql
            self.parameters = parameters

    cursor = Cursor()
    asyncio.run(admin_models._actualizar_binding_aprobado(
        cursor, facet_key="jekyll", model_ref=73, approved_by=9,
    ))

    compact_sql = " ".join(cursor.sql.split())
    assert "provider_id=m.provider_id" in compact_sql
    assert "model_id=m.model_id" in compact_sql
    assert cursor.parameters == (73, 9, "jekyll")
