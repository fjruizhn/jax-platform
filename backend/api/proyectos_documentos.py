"""Documentos de un proyecto (E2a, T6, 2026-10-03): subir por lotes, listar,
ocultar, restaurar y publicar los limites.

Es el punto de entrada de archivos de clientes. Reglas, en el orden en que se
aplican:

1. Papel y visibilidad son los de E1, sin SQL nuevo: `api.proyectos._leer`
   (404 igual para el inexistente, el oculto y el no miembro) y
   `ProjectRoleInsufficient` traducido por `_http` (403). Subir, ocultar y
   restaurar exigen CONTRIBUTOR o mas (CONTRIBUTOR, REVIEWER, OWNER: los tres
   llevan `memory:project:write` en `scope_authority`); listar, VIEWER; la vista
   `ocultos`, CONTRIBUTOR.
2. Todo eso se decide ANTES de leer el cuerpo: el multipart se lee con
   `request.form()` recien despues, asi un lector o un ajeno no hace que la
   plataforma reciba y vuelque a disco su cuerpo entero.
3. `proyecto_no_activo` (409) lo decide la plataforma antes de escribir nada; vale
   para subir, ocultar y restaurar. Esas tres rutas estan en `RUTAS_FRENADAS`.
4. Por archivo: tipo -> nombre seguro -> escritura en streaming con tope sobre lo
   leido -> `repositorio.insertar`; si la restriccion unica `(project_id, sha256)`
   lo rechaza, se borra lo escrito y `existente_por_sha` dice si el duplicado esta
   visible u oculto. Quien decide el duplicado es la base, no un SELECT previo.
5. Las filas quedan `en_cola`. El aviso al despachador lo agrega la Tarea 7.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.datastructures import UploadFile

import ajustes
from adjuntos import cuota
from api.proyectos import _http, _leer, _RutaConCodigo
from auth.middleware import get_current_user
from kill_switch import exigir_mesa_libre
from auth.models import AuthUser
from db.connection import get_pool
from jax.memory.project_authority import ProjectRoleInsufficient
from proyectos_documentos import almacen, tipos
from proyectos_documentos import repositorio as repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", route_class=_RutaConCodigo)

# CONTRIBUTOR, REVIEWER y OWNER escriben (scope_authority: memory:project:write).
_ESCRIBEN = frozenset({"CONTRIBUTOR", "REVIEWER", "OWNER"})
# Lo que el multipart agrega a cada parte (cabeceras, borde, nombre): holgura para
# el rechazo temprano por Content-Length, que NO es la medida de los topes.
_HOLGURA_MULTIPART_BASE = 1024 * 1024
_HOLGURA_MULTIPART_POR_ARCHIVO = 8 * 1024
_CAMPO = "archivos"


def _error(status: int, code: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, **extra})


async def _con_papel(user: AuthUser, project_id: int, *, escribe: bool, activo: bool = False) -> dict:
    """404 si no es visible, 403 si falta el papel y, con `activo`, 409
    `proyecto_no_activo`: en ese orden, para que un lector no aprenda el estado."""
    proyecto = await _leer(user, project_id)
    if escribe and proyecto["papel"] not in _ESCRIBEN:
        raise _http(ProjectRoleInsufficient("this operation requires CONTRIBUTOR"))
    if activo and proyecto["estado"] != "ACTIVE":
        raise _error(409, "proyecto_no_activo")
    return proyecto


@router.get("/proyectos/documentos/limites")
async def limites(user: AuthUser = Depends(get_current_user)):
    return {"max_bytes_archivo": await ajustes.valor(ajustes.DOC_MAX_BYTES_ARCHIVO),
            "max_archivos_lote": await ajustes.valor(ajustes.DOC_MAX_ARCHIVOS_LOTE),
            "max_bytes_lote": await ajustes.valor(ajustes.DOC_MAX_BYTES_LOTE),
            "extensiones": sorted(tipos.EXTENSIONES_ACEPTADAS)}


def _lote_excedido(lote: str, aceptados: list, ignorados: list) -> HTTPException:
    return _error(413, "lote_demasiado_grande", lote=lote, aceptados=aceptados, ignorados=ignorados)


@router.post("/proyectos/{project_id}/documentos", status_code=202)
async def subir(project_id: int, request: Request, user: AuthUser = Depends(exigir_mesa_libre)):
    proyecto = await _con_papel(user, project_id, escribe=True, activo=True)

    max_archivo = int(await ajustes.valor(ajustes.DOC_MAX_BYTES_ARCHIVO))
    max_archivos = int(await ajustes.valor(ajustes.DOC_MAX_ARCHIVOS_LOTE))
    max_lote = int(await ajustes.valor(ajustes.DOC_MAX_BYTES_LOTE))
    try:
        workspace = almacen.cargar_workspace()
    except almacen.WorkspaceNoConfigurado:
        logger.error("proyectos_documentos: %s no esta bien configurado", almacen.VARIABLE_WORKSPACE, exc_info=True)
        raise _error(503, "almacen_no_configurado") from None

    # Rechazo temprano, solo para no recibir un cuerpo que ya se ve imposible: un
    # Content-Length mucho mayor que el tope del lote. Es una cota, no la medida;
    # la medida es lo leido, abajo. (Sin Content-Length -- chunked -- no hay cota.)
    try:
        declarado = int(request.headers.get("content-length", "0"))
    except ValueError:
        declarado = 0
    if declarado > max_lote + _HOLGURA_MULTIPART_BASE + _HOLGURA_MULTIPART_POR_ARCHIVO * max_archivos:
        raise _error(413, "lote_demasiado_grande", lote=None, aceptados=[], ignorados=[])

    formulario = await request.form(max_files=max_archivos + 1)
    try:
        partes = formulario.getlist(_CAMPO)
        if not partes:
            raise _error(422, "datos_invalidos")
        return await _guardar_lote(partes, proyecto=proyecto, user=user, workspace=workspace,
                                   max_archivo=max_archivo, max_archivos=max_archivos, max_lote=max_lote)
    finally:
        await formulario.close()


async def _guardar_lote(partes: list, *, proyecto: dict, user: AuthUser, workspace: Path, max_archivo: int,
                        max_archivos: int, max_lote: int) -> dict:
    pool = await get_pool()
    lote = secrets.token_urlsafe(12)
    carpeta = almacen.carpeta_entrada(workspace, proyecto["uuid"], lote)
    aceptados: list[dict] = []
    ignorados: list[dict] = []
    usados: set[str] = set()
    preparada = False
    bytes_lote = 0
    try:
        for orden, parte in enumerate(partes, start=1):
            if orden > max_archivos:
                raise _lote_excedido(lote, aceptados, ignorados)
            if not isinstance(parte, UploadFile) or not parte.filename:
                ignorados.append({"nombre": "", "motivo": "nombre_invalido"})
                continue
            nombre = almacen.nombre_para_mostrar(parte.filename)
            tipo = tipos.tipo_de(parte.filename)
            if tipo is None:
                ignorados.append({"nombre": nombre, "motivo": "tipo_no_admitido"})
                continue
            seguro = almacen.nombre_seguro(parte.filename, usados)
            if tipos.tipo_de(seguro) != tipo:
                ignorados.append({"nombre": nombre, "motivo": "nombre_invalido"})
                continue

            if not preparada:
                try:
                    await cuota.exigir_disco_libre(workspace, max_lote)
                except cuota.SinEspacio:
                    raise _error(507, "sin_espacio") from None
                await asyncio.to_thread(almacen.preparar_carpeta, carpeta, workspace)
                preparada = True

            destino = carpeta / seguro
            restante = max_lote - bytes_lote
            usados.add(seguro)
            try:
                escritos, sha256 = await almacen.escribir_streaming(parte, destino, min(max_archivo, restante))
            except almacen.DemasiadoGrande:
                usados.discard(seguro)
                if restante < max_archivo:
                    raise _lote_excedido(lote, aceptados, ignorados) from None
                ignorados.append({"nombre": nombre, "motivo": "demasiado_grande"})
                continue
            except BaseException:
                usados.discard(seguro)
                raise
            bytes_lote += escritos

            try:
                nuevo = await repo.insertar(
                    pool, project_id=proyecto["id"], sha256=sha256, nombre_original=nombre,
                    ruta_entrada=destino.relative_to(workspace).as_posix(), bytes_=escritos, tipo=tipo,
                    subido_por=int(user.user_id))
                if nuevo is None:
                    existente = await repo.existente_por_sha(pool, project_id=proyecto["id"], sha256=sha256)
            except BaseException:
                await asyncio.to_thread(destino.unlink, missing_ok=True)
                usados.discard(seguro)
                raise
            if nuevo is None:
                await asyncio.to_thread(destino.unlink, missing_ok=True)
                usados.discard(seguro)
                oculto = existente is not None and existente["oculto"]
                ignorados.append({"nombre": nombre, "motivo": "duplicado_oculto" if oculto else "duplicado"})
                continue
            aceptados.append({"id": nuevo, "nombre": nombre})
    finally:
        if preparada and not aceptados:
            await asyncio.to_thread(_quitar_si_vacia, carpeta)
    return {"lote": lote, "aceptados": aceptados, "ignorados": ignorados}


def _quitar_si_vacia(carpeta: Path) -> None:
    try:
        carpeta.rmdir()
    except OSError:  # fail-soft: la carpeta tiene archivos (otro lote ya la uso) o ya no existe; es limpieza, no un resultado
        pass


@router.get("/proyectos/{project_id}/documentos")
async def listar(project_id: int, vista: Literal["visibles", "ocultos"] = "visibles",
                 antes_de: int | None = Query(default=None, ge=1), limite: int = Query(default=50, ge=1, le=100),
                 user: AuthUser = Depends(get_current_user)):
    proyecto = await _con_papel(user, project_id, escribe=vista == "ocultos")
    filas = await repo.listar(await get_pool(), project_id=proyecto["id"], ocultos=vista == "ocultos",
                              antes_de=antes_de, limite=limite + 1)
    siguiente = filas[limite - 1]["id"] if len(filas) > limite else None
    return {"documentos": filas[:limite], "siguiente": siguiente}


async def _cambiar_visibilidad(project_id: int, documento_id: int, user: AuthUser, *, ocultar: bool) -> Response:
    proyecto = await _con_papel(user, project_id, escribe=True, activo=True)
    pool = await get_pool()
    if ocultar:
        existe = await repo.ocultar(pool, project_id=proyecto["id"], documento_id=documento_id,
                                    user_id=int(user.user_id))
    else:
        existe = await repo.restaurar(pool, project_id=proyecto["id"], documento_id=documento_id)
    if not existe:
        raise _error(404, "documento_no_encontrado")
    return Response(status_code=204)


@router.post("/proyectos/{project_id}/documentos/{documento_id}/ocultar", status_code=204)
async def ocultar(project_id: int, documento_id: int, user: AuthUser = Depends(exigir_mesa_libre)):
    return await _cambiar_visibilidad(project_id, documento_id, user, ocultar=True)


@router.post("/proyectos/{project_id}/documentos/{documento_id}/restaurar", status_code=204)
async def restaurar(project_id: int, documento_id: int, user: AuthUser = Depends(exigir_mesa_libre)):
    return await _cambiar_visibilidad(project_id, documento_id, user, ocultar=False)
