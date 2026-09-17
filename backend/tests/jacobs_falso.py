"""Jacobs falso para los tests de api/pipelines.py (spec 2026-09-17).

Respeta EXACTAMENTE el contrato del plan (sección "Contrato con Jacobs" y su
ENMIENDA en task-5-brief.md, que implementa el plan J en el repo jax):
errores con el envoltorio de FastAPI {"detail": {...}} y montos como string
decimal. POST /preflight recibe {invoked_by, user_id, tenant_id, steps,
objective} (objective "" si el cliente no lo mandó; revisión final, crítico 1:
Jacobs cuenta el objetivo en el costo). Si el contrato cambia, se cambia acá y en api/pipelines.py, en
ningún otro lado.

No es un test (no empieza con test_): pytest no lo colecta."""
from decimal import Decimal
from types import SimpleNamespace

import httpx

URL = "http://jacobs.test/jacobs"


def respuesta(status: int, cuerpo=None, texto: str | None = None) -> httpx.Response:
    if texto is not None:
        return httpx.Response(status, text=texto)
    return httpx.Response(status, json=cuerpo)


def paso_costo(paso: int = 0, faceta: str = "jekyll", usd: str | None = "0.10", motivo: str | None = None) -> dict:
    return {"paso": paso, "faceta": faceta, "modelo": "modelo-de-prueba", "llamadas_max": 1,
            "tokens_in_max": 100, "tokens_out_max": 1000, "usd_max": usd, "motivo": motivo}


def violacion(paso: int = 4, faceta: str = "kimi", regla: str = "tope_insuficiente",
              detalle: str = "tope 8000 < minimo 16384") -> dict:
    return {"paso": paso, "faceta": faceta, "regla": regla, "detalle": detalle}


def veredicto(ok: bool = True, costo: str = "0.10", violaciones=(), pasos_costo=None, sondeadas=(),
              hay_no_acotados: bool | None = None) -> dict:
    v = {
        "ok": ok,
        "violaciones": list(violaciones),
        "costo_max_usd": costo,
        "pasos_costo": list(pasos_costo) if pasos_costo is not None else [paso_costo(usd=costo)],
        "sondeadas": list(sondeadas),
    }
    # ENMIENDA ítem 4 (2026-09-17): Jacobs puede declarar hay_no_acotados;
    # si el llamador no lo pide, el veredicto no lo trae (la Mesa lo
    # recalcula localmente desde usd_max).
    if hay_no_acotados is not None:
        v["hay_no_acotados"] = hay_no_acotados
    return v


def invalida(paso=5, motivo: str = "solo se reasignan pasos a correr, no los reusados") -> dict:
    """Un elemento del `detalle` de reasignacion_invalida cuando el índice o
    la faceta no sirven (jax: jacobs/continuar.py `analizar`)."""
    return {"paso": paso, "motivo": motivo}


def violacion_de_plan(step_index: int = 4, facet: str = "ada", motor: str | None = None,
                      capability: str = "web_search", reason: str = "clean-room: ada no puede web_search") -> dict:
    """PlanViolation.to_dict() de jax (jacobs/plan.py): el `detalle` de
    reasignacion_invalida o plan_rechazado cuando falla clean-room o capability."""
    return {"step_index": step_index, "facet": facet, "motor": motor, "capability": capability, "reason": reason}


def motivo(code: str, detalle=None, **campos) -> dict:
    """ContinuarRechazado.cuerpo() de jax: {code, **dict} o {code, detalle}."""
    cuerpo = {"code": code, **campos}
    if detalle is not None:
        cuerpo["detalle"] = detalle
    return cuerpo


# Con estos motivos Jacobs SÍ analizó el pipeline y trae pasos y veredicto
# (jacobs/continuar.py `previsualizar`); con cualquier otro, no.
MOTIVOS_CON_VEREDICTO = ("prevuelo_rechazado", "limite_de_activos")


def previsualizacion(v=None, motivo_=None, a_correr=(4, 5), reusados=(0, 1, 2, 3)) -> dict:
    """200 de POST /pipeline/{id}/continue/preflight, con la forma exacta de jax."""
    if motivo_ is not None and motivo_.get("code") not in MOTIVOS_CON_VEREDICTO:
        return {"continuable": False, "motivo": motivo_, "pasos_a_correr": [], "pasos_reusados": [],
                "veredicto": None}
    return {"continuable": motivo_ is None, "motivo": motivo_, "pasos_a_correr": list(a_correr),
            "pasos_reusados": list(reusados), "veredicto": v}


class JacobsFalso:
    """rutas: {("POST", "/preflight"): httpx.Response | callable(cuerpo) -> httpx.Response}.
    Una ruta no declarada lanza AssertionError (la Mesa la convierte en 502:
    el test que llegó adonde no esperaba falla por status)."""

    def __init__(self, rutas=None):
        self.rutas = dict(rutas or {})
        self.llamadas: list[tuple[str, str, dict | None]] = []

    def _responder(self, metodo: str, url: str, cuerpo):
        assert url.startswith(URL), url
        ruta = url[len(URL):]
        self.llamadas.append((metodo, ruta, cuerpo))
        r = self.rutas.get((metodo, ruta))
        if r is None:
            raise AssertionError(f"Jacobs falso: ruta no declarada {metodo} {ruta}")
        return r(cuerpo) if callable(r) else r

    @staticmethod
    def _exigir_credencial(headers):
        # LAS MANOS real (jax las_manos/auth_servicio.py) rechaza todo pedido
        # sin la credencial de servicio: el falso tampoco lo deja pasar.
        import os
        from credencial_las_manos import ENCABEZADO, VARIABLE
        assert headers and headers.get(ENCABEZADO) == os.environ[VARIABLE], \
            "Jacobs falso: pedido sin la credencial de servicio de la plataforma"

    async def post(self, url, json=None, timeout=None, headers=None):
        self._exigir_credencial(headers)
        return self._responder("POST", url, json)

    async def get(self, url, timeout=None, headers=None):
        self._exigir_credencial(headers)
        return self._responder("GET", url, None)

    def cuerpos(self, metodo: str, ruta: str) -> list:
        return [c for m, r, c in self.llamadas if (m, r) == (metodo, ruta)]


def preparar(monkeypatch, falso: JacobsFalso, umbral: str = "0.50", maximo: int = 3,
             cupo_libre: bool = True, nombre: str = "nombre del pipeline") -> SimpleNamespace:
    """Instala el Jacobs falso y aísla todo lo que no es Jacobs: ajustes,
    cupo, dueño, recurso y eventos de WS quedan en memoria y se registran."""
    import api.pipelines as mod

    registro = SimpleNamespace(admitidos=[], publicados=[], duenios=[], removidos=[], liberados=[])
    valores = {mod.ajustes.MAX_PIPELINES: maximo, mod.ajustes.CONFIRMAR_USD: Decimal(umbral)}

    async def cliente():
        return falso

    async def valor(clave):
        return valores[clave]

    async def cupo(_tenant, _limite):
        return cupo_libre

    async def duenio(pipeline_id, _user):
        registro.duenios.append(pipeline_id)
        return nombre

    async def registrar_duenio(pipeline_id, _tenant_id, _user_id):
        registro.duenios.append(pipeline_id)

    async def admitir(tenant_id, pipeline_id):
        registro.admitidos.append((tenant_id, pipeline_id))

    def remover(pipeline_id):
        registro.removidos.append(pipeline_id)

    async def liberar(tenant_id, pipeline_id):
        registro.liberados.append((tenant_id, pipeline_id))

    async def upsert(pipeline, _tenant_id, _user_id):
        registro.publicados.append(("pipeline_step_changed", pipeline.pipeline_id, {}))

    async def continuar(pipeline, _tenant_id, _user_id, continuacion):
        registro.publicados.append(("pipeline_continued", pipeline.pipeline_id, continuacion))

    monkeypatch.setattr(mod, "get_http_client", cliente)
    monkeypatch.setattr(mod, "JACOBS_URL", URL)
    monkeypatch.setattr(mod.ajustes, "valor", valor)
    monkeypatch.setattr(mod.resource_manager, "can_start_pipeline", cupo)
    monkeypatch.setattr(mod.resource_manager, "admit_pipeline", admitir)
    monkeypatch.setattr(mod.engine_state, "remove_pipeline", remover)
    monkeypatch.setattr(mod.resource_manager, "release_pipeline", liberar)
    monkeypatch.setattr(mod, "_require_pipeline_owner", duenio)
    monkeypatch.setattr(mod, "_record_pipeline_owner", registrar_duenio)
    monkeypatch.setattr(mod.engine_state, "upsert_pipeline", upsert)
    monkeypatch.setattr(mod.engine_state, "continuar_pipeline", continuar, raising=False)
    return registro
