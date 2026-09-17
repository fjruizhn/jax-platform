import facet_resolver
from tests.identidades import cabeceras


async def _fetch(sql, args=()):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            return await cur.fetchall()


async def _commit(sql, args=()):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
        await conn.commit()


_MODELO_DE = ("SELECT m.id, m.input_modalities FROM facet_binding b JOIN model m ON m.id = b.model_ref "
              "WHERE b.facet_key = %s AND b.role = 'primary'")


def test_politica_dice_limites_tipos_y_que_facetas_ven_imagenes(client, monkeypatch):
    monkeypatch.setenv("JAX_ADJUNTO_MAX_BYTES", "1234")
    (ref_h, antes_h), = client.portal.call(_fetch, _MODELO_DE, ("hipatia",))
    (ref_j, antes_j), = client.portal.call(_fetch, _MODELO_DE, ("jekyll",))
    try:
        client.portal.call(_commit, "UPDATE model SET input_modalities='text,image' WHERE id=%s", (ref_h,))
        if ref_j != ref_h:
            client.portal.call(_commit, "UPDATE model SET input_modalities='text' WHERE id=%s", (ref_j,))
        r = client.get("/api/chat/adjuntos", headers=cabeceras(client, "test-adjuntos-politica"))
        assert r.status_code == 200, r.text
        cuerpo = r.json()
        assert cuerpo["max_bytes"] == 1234
        assert cuerpo["mimes_de_imagen"] == ["image/png", "image/jpeg", "image/webp"]
        assert "application/pdf" in cuerpo["accept"] and ".md" in cuerpo["accept"]
        assert "image/gif" not in cuerpo["accept"]
        assert "hipatia" in cuerpo["facetas_con_imagen"]
        if ref_j != ref_h:
            assert "jekyll" not in cuerpo["facetas_con_imagen"]
    finally:
        client.portal.call(_commit, "UPDATE model SET input_modalities=%s WHERE id=%s", (antes_h, ref_h))
        client.portal.call(_commit, "UPDATE model SET input_modalities=%s WHERE id=%s", (antes_j, ref_j))
        facet_resolver._cache.clear()


def test_politica_sin_sesion_es_401(client):
    assert client.get("/api/chat/adjuntos").status_code == 401
