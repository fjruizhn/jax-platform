"""Proyectos (E1, 2026-10-02): API sobre la autoridad de B9.

La plataforma NO decide papeles: toda mutación va a `ProjectAuthorityAdmin`
y toda lectura a `jax.memory.project_queries`, que releen la identidad en la
base. Aquí solo se arman el ScopeContext desde el usuario autenticado y se
traducen las excepciones de B9 a códigos HTTP estables (spec E1 §5.2).
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from auth.middleware import get_current_user
from auth.models import AuthUser
from db.connection import get_pool
from b9_pool import B9MappingPool
from jax.memory.b9 import AuthorizationDenied, MutationAuthorizationRequest, ScopeContext, Visibility
from jax.memory.b9_mariadb import MariaDBB9Store
from jax.memory.project_authority import (
    AlreadyMember, IdempotencyKeyConflict, InvalidIdempotencyKey, LastOwnerRequired, MemberNotFound,
    ProjectAuthorityAdmin, ProjectAuthorityRetryable, ProjectNotVisible, ProjectRoleInsufficient,
    ProjectStateConflict, ReservedProjectIdRange, TargetUserNotEligible, TenantAdminMembershipProtected,
)
from jax.memory.project_queries import (ProjectView, get_project_for_user, list_invite_candidates,
                                        list_project_members, list_projects_for_user)
from jax.memory.scope_authority import ProjectLifecycle, ProjectRole

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# Orden importa: ProjectNotVisible hereda de ScopeDenied/AuthorizationDenied.
_HTTP_DE_ERROR: tuple[tuple[type[Exception], int, str], ...] = (
    (ProjectNotVisible, 404, "proyecto_no_encontrado"),
    (MemberNotFound, 404, "miembro_no_encontrado"),
    (ProjectRoleInsufficient, 403, "papel_insuficiente"),
    (ProjectStateConflict, 409, "estado_no_permite"),
    (LastOwnerRequired, 409, "ultimo_dueno"),
    (TenantAdminMembershipProtected, 409, "admin_protegido"),
    (AlreadyMember, 409, "ya_es_miembro"),
    (TargetUserNotEligible, 422, "usuario_no_elegible"),
    (IdempotencyKeyConflict, 409, "idempotencia_conflicto"),
    (InvalidIdempotencyKey, 422, "idempotencia_invalida"),
    (ProjectAuthorityRetryable, 503, "reintentar"),
    (ReservedProjectIdRange, 500, "proyecto_id_reservado"),
    (AuthorizationDenied, 422, "datos_invalidos"),
)

_PAPELES_ASIGNABLES = Literal["VIEWER", "CONTRIBUTOR", "OWNER"]
_ESTADOS = Literal["ACTIVE", "ARCHIVED", "HIDDEN"]


def _http(exc: Exception) -> HTTPException:
    for clase, status, code in _HTTP_DE_ERROR:
        if isinstance(exc, clase):
            if status >= 500:
                logger.error("proyectos: %s", code, exc_info=exc)
            return HTTPException(status_code=status, detail={"code": code})
    logger.error("proyectos: error inesperado", exc_info=exc)
    return HTTPException(status_code=500, detail={"code": "proyectos_error"})


def _ids(user: AuthUser) -> tuple[int, int]:
    if not user.tenant_id:
        raise HTTPException(status_code=403, detail={"code": "tenant_scope_required"})
    return int(user.tenant_id), int(user.user_id)


def _request(user: AuthUser, operation: str, project_id: int | None) -> MutationAuthorizationRequest:
    scope = ScopeContext(actor_principal=f"user:{user.user_id}", actor_type="USER",
                         subject_user_id=str(user.user_id), tenant_id=str(user.tenant_id),
                         project_id=str(project_id) if project_id is not None else None,
                         calling_component="jax-platform-proyectos")
    return MutationAuthorizationRequest(scope, operation, Visibility.PROJECT_SHARED)


async def _pool() -> B9MappingPool:
    return B9MappingPool(await get_pool())


async def _admin() -> ProjectAuthorityAdmin:
    return ProjectAuthorityAdmin(MariaDBB9Store(await _pool()))


def _out(p: dict) -> dict:
    return {"id": p["project_id"], "uuid": p["project_uuid"], "nombre": p["name"],
            "descripcion": p["description"], "estado": p["status"], "papel": p["role"]}


def _descripcion(valor: str | None) -> str | None:
    """Vacía o solo espacios es "sin descripción": B9 recibe None, no ""."""
    if valor is None or not valor.strip():
        return None
    return valor


class ProyectoIn(BaseModel):
    """Cuerpo de POST y de PATCH. En PATCH `descripcion` es OBLIGATORIA (puede
    ser null): rename_project reemplaza la descripción entera, y un PATCH que
    solo mandara el nombre la borraría en silencio."""
    nombre: str = Field(min_length=1, max_length=255)
    descripcion: str | None = Field(max_length=2000)


class ProyectoCrearIn(ProyectoIn):
    descripcion: str | None = Field(default=None, max_length=2000)


class EstadoIn(BaseModel):
    estado: _ESTADOS


class MiembroIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    papel: _PAPELES_ASIGNABLES


class PapelIn(BaseModel):
    papel: _PAPELES_ASIGNABLES


@router.get("/proyectos")
async def listar(vista: ProjectView = ProjectView.ACTIVOS, antes_de: int | None = Query(default=None, ge=1),
                 limite: int = Query(default=50, ge=1, le=100), user: AuthUser = Depends(get_current_user)):
    tenant_id, user_id = _ids(user)
    try:
        filas = await list_projects_for_user(await _pool(), tenant_id=tenant_id, user_id=user_id,
                                             view=vista, before_id=antes_de, limit=limite + 1)
    except Exception as exc:
        raise _http(exc) from exc
    siguiente = filas[limite - 1]["project_id"] if len(filas) > limite else None
    return {"proyectos": [_out(p) for p in filas[:limite]], "siguiente": siguiente}


async def _leer(user: AuthUser, project_id: int) -> dict:
    tenant_id, user_id = _ids(user)
    try:
        return _out(await get_project_for_user(await _pool(), tenant_id=tenant_id, user_id=user_id,
                                               project_id=project_id))
    except Exception as exc:
        raise _http(exc) from exc


@router.post("/proyectos", status_code=201)
async def crear(body: ProyectoCrearIn, response: Response, user: AuthUser = Depends(get_current_user),
                idempotency_key: str = Header(alias="Idempotency-Key")):
    _ids(user)
    try:
        creado = await (await _admin()).create_project(_request(user, "CREATE_PROJECT", None),
                                                       name=body.nombre, description=_descripcion(body.descripcion),
                                                       idempotency_key=idempotency_key)
    except Exception as exc:
        raise _http(exc) from exc
    if not creado.created:
        response.status_code = 200
    return await _leer(user, creado.project_id)


@router.get("/proyectos/{project_id}")
async def ver(project_id: int, user: AuthUser = Depends(get_current_user)):
    return await _leer(user, project_id)


@router.patch("/proyectos/{project_id}")
async def renombrar(project_id: int, body: ProyectoIn, user: AuthUser = Depends(get_current_user)):
    _ids(user)
    try:
        await (await _admin()).rename_project(_request(user, "RENAME_PROJECT", project_id), project_id,
                                              name=body.nombre, description=_descripcion(body.descripcion))
    except Exception as exc:
        raise _http(exc) from exc
    return await _leer(user, project_id)


@router.post("/proyectos/{project_id}/estado")
async def cambiar_estado(project_id: int, body: EstadoIn, user: AuthUser = Depends(get_current_user)):
    _ids(user)
    try:
        await (await _admin()).set_project_lifecycle(_request(user, "SET_PROJECT_LIFECYCLE", project_id),
                                                     project_id, ProjectLifecycle(body.estado))
    except Exception as exc:
        raise _http(exc) from exc
    return await _leer(user, project_id)


@router.get("/proyectos/{project_id}/miembros")
async def miembros(project_id: int, user: AuthUser = Depends(get_current_user)):
    tenant_id, user_id = _ids(user)
    try:
        filas = await list_project_members(await _pool(), tenant_id=tenant_id, user_id=user_id,
                                           project_id=project_id)
    except Exception as exc:
        raise _http(exc) from exc
    return {"miembros": [{"user_id": m["user_id"], "email": m["email"], "papel": m["role"],
                          "origen": m["grant_origin"]} for m in filas]}


@router.post("/proyectos/{project_id}/miembros", status_code=201)
async def invitar(project_id: int, body: MiembroIn, user: AuthUser = Depends(get_current_user)):
    _ids(user)
    try:
        nuevo = await (await _admin()).grant_member(_request(user, "GRANT_MEMBER", project_id), project_id,
                                                    email=body.email, role=ProjectRole(body.papel))
    except Exception as exc:
        raise _http(exc) from exc
    return {"user_id": nuevo}


@router.patch("/proyectos/{project_id}/miembros/{user_id}", status_code=204)
async def cambiar_papel(project_id: int, user_id: int, body: PapelIn, user: AuthUser = Depends(get_current_user)):
    _ids(user)
    try:
        await (await _admin()).change_project_role(_request(user, "CHANGE_PROJECT_ROLE", project_id),
                                                   project_id, user_id, ProjectRole(body.papel))
    except Exception as exc:
        raise _http(exc) from exc
    return Response(status_code=204)


@router.delete("/proyectos/{project_id}/miembros/{user_id}", status_code=204)
async def quitar(project_id: int, user_id: int, user: AuthUser = Depends(get_current_user)):
    _ids(user)
    try:
        await (await _admin()).revoke_member(_request(user, "REVOKE_MEMBER", project_id), project_id, user_id)
    except Exception as exc:
        raise _http(exc) from exc
    return Response(status_code=204)


@router.get("/proyectos/{project_id}/candidatos")
async def candidatos(project_id: int, q: str = Query(default="", max_length=320),
                     user: AuthUser = Depends(get_current_user)):
    tenant_id, user_id = _ids(user)
    try:
        filas = await list_invite_candidates(await _pool(), tenant_id=tenant_id, user_id=user_id,
                                             project_id=project_id, query=q, limit=20)
    except Exception as exc:
        raise _http(exc) from exc
    return {"candidatos": filas}
