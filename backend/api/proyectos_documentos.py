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
5. Las filas quedan `en_cola`. Si el lote acepto al menos una, se avisa al despachador
   (`despachador.despachar_ahora()`, sin esperarlo); si falla, la subida igual responde bien:
   el despachador de fondo las toma en su siguiente vuelta.
"""
from __future__ import annotations

import asyncio
import errno
import logging
import secrets
from pathlib import Path
from typing import Literal

from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.datastructures import UploadFile

import ajustes
from adjuntos import cuota
from api.proyectos import _http, _leer, _RutaConCodigo
from auth.middleware import get_current_user
from auth.models import AuthUser
from db.connection import get_pool
from jax.memory.project_authority import ProjectNotVisible, ProjectRoleInsufficient
from jax.memory.scope_authority import MariaDBScopeAuthorityResolver, ProjectRole
from kill_switch import exigir_freno_suelto, exigir_mesa_libre
from proyectos_documentos import almacen, despachador, tipos
from proyectos_documentos import repositorio as repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", route_class=_RutaConCodigo)

# Lo que el multipart agrega a cada parte (cabeceras, borde, nombre): holgura para
# el rechazo temprano por Content-Length, que NO es la medida de los topes.
_HOLGURA_MULTIPART_BASE = 1024 * 1024
_HOLGURA_MULTIPART_POR_ARCHIVO = 8 * 1024
_CAMPO = "archivos"


def _error(status: int, code: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, **extra})


def _puede_escribir(papel: str) -> bool:
    """La decision de B9, no una tabla propia: quien tiene la capacidad
    `memory:project:write` en `MariaDBScopeAuthorityResolver._project_roles` (hoy CONTRIBUTOR,
    REVIEWER y OWNER) puede subir, ocultar y restaurar."""
    return "memory:project:write" in MariaDBScopeAuthorityResolver._project_roles(ProjectRole(papel))[1]


# Los papeles que B9 deja escribir, calculados UNA vez con la misma capacidad: el INSERT
# los usa para volver a exigir la membresia sin una segunda tabla.
_ROLES_DE_ESCRITURA = tuple(r.value for r in ProjectRole if _puede_escribir(r.value))


def _nuevo_lote() -> str:
    return secrets.token_urlsafe(12)


async def _con_papel(user: AuthUser, project_id: int, *, escribe: bool, activo: bool = False) -> dict:
    """404 si no es visible, 403 si falta el papel y, con `activo`, 409
    `proyecto_no_activo`: en ese orden, para que un lector no aprenda el estado."""
    proyecto = await _leer(user, project_id)
    if escribe and not _puede_escribir(proyecto["papel"]):
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

    try:
        formulario = await request.form(max_files=max_archivos + 1)
    except StarletteHTTPException as exc:
        # Starlette corta el multipart con 400 al pasar max_files (o max_fields): para
        # el cliente es el mismo "lote demasiado grande", y no se escribio nada.
        if exc.status_code == 400 and str(exc.detail).startswith("Too many"):
            raise _error(413, "lote_demasiado_grande", lote=None, aceptados=[], ignorados=[]) from None
        raise
    try:
        # Recibir el cuerpo puede tardar: el freno, el papel y el estado ACTIVE se
        # vuelven a mirar ANTES de tocar el disco. (El INSERT lo vuelve a exigir.)
        exigir_freno_suelto()
        proyecto = await _con_papel(user, project_id, escribe=True, activo=True)
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
    lote = _nuevo_lote()
    carpeta: almacen.CarpetaLote | None = None
    aceptados: list[dict] = []
    ignorados: list[dict] = []
    usados: set[str] = set()
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

            if carpeta is None:
                try:
                    await cuota.exigir_disco_libre(workspace, max_lote)
                except cuota.SinEspacio:
                    raise _error(507, "sin_espacio") from None
                try:
                    carpeta = await asyncio.to_thread(almacen.abrir_carpeta_lote, workspace, proyecto["uuid"], lote)
                except almacen.RutaInsegura:
                    # Un enlace simbolico en proyectos/<uuid>/entrada/<lote>: el detalle va
                    # al log, nunca al cuerpo.
                    logger.error("proyectos_documentos: ruta insegura en el workspace", exc_info=True)
                    raise _error(500, "almacen_ruta_insegura") from None

            restante = max_lote - bytes_lote
            usados.add(seguro)
            try:
                escritos, sha256 = await almacen.escribir_streaming(parte, carpeta, seguro, min(max_archivo, restante))
            except almacen.DemasiadoGrande:
                usados.discard(seguro)
                if restante < max_archivo:
                    raise _lote_excedido(lote, aceptados, ignorados) from None
                ignorados.append({"nombre": nombre, "motivo": "demasiado_grande"})
                continue
            except OSError as exc:
                # Con el nombre ya ocupado (un enlace plantado en el lote: O_EXCL da EEXIST)
                # o el disco fallando: se responde con lo que SI quedo registrado en este
                # lote, para que el cliente no lo pierda de vista.
                usados.discard(seguro)
                insegura = isinstance(exc, FileExistsError) or exc.errno == errno.ELOOP
                logger.error("proyectos_documentos: no se pudo escribir %r en el lote %s (%s)", seguro, lote,
                             "ruta insegura" if insegura else "error de disco", exc_info=True)
                raise _error(500, "almacen_ruta_insegura" if insegura else "almacen_error_escritura",
                             lote=lote, aceptados=aceptados, ignorados=ignorados) from None
            except BaseException:
                usados.discard(seguro)
                raise
            bytes_lote += escritos

            try:
                nuevo = await repo.insertar(
                    pool, project_id=proyecto["id"], sha256=sha256, nombre_original=nombre,
                    ruta_entrada=carpeta.ruta_relativa(seguro), bytes_=escritos, tipo=tipo,
                    subido_por=int(user.user_id), roles_escritura=_ROLES_DE_ESCRITURA)
            except (repo.ProyectoNoActivo, repo.MembresiaPerdida) as exc:
                # Rechazo DEFINITIVO del INSERT: nada se inserto, el archivo sobra.
                await asyncio.to_thread(carpeta.borrar, seguro)
                usados.discard(seguro)
                bytes_lote -= escritos
                if isinstance(exc, repo.ProyectoNoActivo):
                    raise _error(409, "proyecto_no_activo", lote=lote, aceptados=aceptados,
                                 ignorados=ignorados) from None
                error = _http(ProjectNotVisible("member lost") if exc.papel is None
                              else ProjectRoleInsufficient("role lost"))
                error.detail = {**error.detail, "lote": lote, "aceptados": aceptados, "ignorados": ignorados}
                raise error from None
            except BaseException as exc:
                # Resultado INCIERTO (p. ej. se cayo la conexion despues de mandar el
                # COMMIT): la fila pudo quedar escrita. NO se toca el disco -- borrar el
                # archivo de una fila que existe seria perder el documento --; se deja
                # el rastro para conciliar a mano.
                logger.error("proyectos_documentos: insercion incierta, lote=%s archivo=%r sha256=%s "
                             "(el archivo queda en disco; conciliar)", lote, seguro, sha256, exc_info=True)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise _error(500, "insercion_incierta", lote=lote, aceptados=aceptados,
                             ignorados=ignorados) from None
            if nuevo is None:
                await asyncio.to_thread(carpeta.borrar, seguro)
                usados.discard(seguro)
                bytes_lote -= escritos          # el duplicado descartado no ocupa lugar en el lote
                try:
                    existente = await repo.existente_por_sha(pool, project_id=proyecto["id"], sha256=sha256)
                except Exception:
                    # El duplicado ya esta descartado (archivo borrado); lo que falla es solo
                    # decir si estaba oculto. Se responde con lo que SI quedo registrado.
                    logger.error("proyectos_documentos: no se pudo consultar el duplicado %r (lote %s)", seguro,
                                 lote, exc_info=True)
                    raise _error(500, "consulta_duplicado_fallida", lote=lote, aceptados=aceptados,
                                 ignorados=ignorados) from None
                oculto = existente is not None and existente["oculto"]
                ignorados.append({"nombre": nombre, "motivo": "duplicado_oculto" if oculto else "duplicado"})
                continue
            aceptados.append({"id": nuevo, "nombre": nombre})
    finally:
        if carpeta is not None:
            if not aceptados:
                await asyncio.to_thread(carpeta.quitar_lote_si_vacio)
            carpeta.cerrar()
    if aceptados:
        try:
            despachador.despachar_ahora()
        except Exception:  # fail-soft: el aviso es un adelanto; las filas ya estan en_cola y el despachador de fondo las toma en su vuelta
            logger.warning("proyectos_documentos: no se pudo avisar al despachador (lote %s)", lote, exc_info=True)
    return {"lote": lote, "aceptados": aceptados, "ignorados": ignorados}


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
