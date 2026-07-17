from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from auth.middleware import require_superadmin
from auth.models import AuthUser
from db.connection import get_pool

router = APIRouter(prefix="/api/admin")


@router.get("/facet-models/{facet}")
async def list_facet_models(facet: str, user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, facet, provider_id, model_name, is_active, added_by, created_at "
                "FROM facet_models WHERE facet = %s ORDER BY is_active DESC, id ASC",
                (facet,),
            )
            rows = await cur.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"Faceta '{facet}' sin modelos registrados")

    return {
        "facet": facet,
        "models": [
            {
                "id": r[0],
                "facet": r[1],
                "provider_id": r[2],
                "model_name": r[3],
                "is_active": bool(r[4]),
                "added_by": r[5],
                "created_at": r[6].isoformat() if r[6] else None,
            }
            for r in rows
        ],
    }


class NewModelRequest(BaseModel):
    provider_id: str
    model_name: str


@router.post("/facet-models/{facet}")
async def add_facet_model(facet: str, req: NewModelRequest, user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(
                    "INSERT INTO facet_models (facet, provider_id, model_name, is_active, added_by) "
                    "VALUES (%s, %s, %s, FALSE, %s)",
                    (facet, req.provider_id, req.model_name, user.email),
                )
            except Exception as e:
                if "Duplicate entry" in str(e):
                    raise HTTPException(status_code=400, detail="Ese modelo ya existe para esta faceta")
                raise
            model_id = cur.lastrowid

    return {"ok": True, "id": model_id}


@router.put("/facet-models/{facet}/active/{model_id}")
async def set_active_facet_model(facet: str, model_id: int, user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id FROM facet_models WHERE id = %s AND facet = %s",
                (model_id, facet),
            )
            row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Modelo no encontrado para esta faceta")

            # Sin trigger de exclusividad (MariaDB rechaza que un trigger
            # modifique la misma tabla que lo disparo, ERROR 1442): se aplica
            # aqui, a nivel de aplicacion, en una transaccion de 2 UPDATE.
            await conn.begin()
            try:
                await cur.execute(
                    "UPDATE facet_models SET is_active = FALSE WHERE facet = %s",
                    (facet,),
                )
                await cur.execute(
                    "UPDATE facet_models SET is_active = TRUE WHERE id = %s",
                    (model_id,),
                )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    return {"ok": True}


@router.delete("/facet-models/{facet}/{model_id}")
async def delete_facet_model(facet: str, model_id: int, user: AuthUser = Depends(require_superadmin)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT is_active FROM facet_models WHERE id = %s AND facet = %s",
                (model_id, facet),
            )
            row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Modelo no encontrado para esta faceta")
            if row[0]:
                raise HTTPException(
                    status_code=400,
                    detail="No se puede eliminar el modelo activo — activa otro primero",
                )

            await cur.execute(
                "DELETE FROM facet_models WHERE id = %s AND facet = %s",
                (model_id, facet),
            )

    return {"ok": True}
