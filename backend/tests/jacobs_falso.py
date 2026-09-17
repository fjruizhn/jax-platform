"""Jacobs falso para los tests de api/pipelines.py (spec 2026-09-17).

Respeta EXACTAMENTE el contrato del plan (sección "Contrato con Jacobs" y su
ENMIENDA en task-5-brief.md, que implementa el plan J en el repo jax):
errores con el envoltorio de FastAPI {"detail": {...}} y montos como string
decimal. Si el contrato cambia, se cambia acá y en api/pipelines.py, en
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

    async def post(self, url, json=None, timeout=None):
        return self._responder("POST", url, json)

    async def get(self, url, timeout=None):
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
