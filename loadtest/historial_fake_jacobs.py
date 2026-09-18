"""Jacobs falso para la carga del historial (Task 10, 2026-09-18).

Sirve GET /health y GET /jacobs/pipeline/<id>/results con un cuerpo GRANDE y
realista (PASOS_DEL_DETALLE pasos, ~CHARS_POR_CAMPO caracteres de prompt +
otros tantos de salida cada uno) para el pipeline que el orquestador le pasa
por CARGA_BIG_PIPELINE_ID -- mide el costo de la Mesa (auth, dueño, proxy,
saneo), no el de Jacobs, mismo criterio que backend/tests/jacobs_falso.py
pero por HTTP real y con el tamaño de cuerpo que describe el brief de la
Task 10, para no repetir la trampa de U5 (un literal en vez de datos de
verdad).

NUNCA escucha en el puerto real de LAS MANOS (7777) ni en el de jax-platform
(8080): revienta al arrancar si CARGA_FAKE_JACOBS_PORT apunta a cualquiera de
los dos.

USO: lo levanta loadtest/historial_orquestar.py; para correrlo suelto:
    CARGA_BIG_PIPELINE_ID=<uuid> \
    JAX_LAS_MANOS_CREDENCIAL_PLATAFORMA=<de prueba> \
    CARGA_FAKE_JACOBS_PORT=17777 \
    python3 loadtest/historial_fake_jacobs.py
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------------------
# CONSTANTES DEL PEOR CASO -- las mismas que documenta docs/carga-historial-2026-09-18.md.
# ---------------------------------------------------------------------------
PASOS_DEL_DETALLE = 6
CHARS_POR_CAMPO = 80_000  # ~20.000 tokens a 4 caracteres/token
PUERTOS_PROHIBIDOS = {7777, 8080}  # LAS MANOS real / jax-platform real -- nunca

BIG_PIPELINE_ID = os.environ["CARGA_BIG_PIPELINE_ID"]
CREDENCIAL_ESPERADA = os.environ["JAX_LAS_MANOS_CREDENCIAL_PLATAFORMA"]
ENCABEZADO = "X-Jax-Credencial-Servicio"

_PUERTO = int(os.environ.get("CARGA_FAKE_JACOBS_PORT", "17777"))
if _PUERTO in PUERTOS_PROHIBIDOS:
    raise RuntimeError(
        f"CARGA_FAKE_JACOBS_PORT={_PUERTO}: ese es un puerto de PRODUCCIÓN "
        f"(LAS MANOS 7777 / jax-platform 8080) -- ABORTANDO, no se levanta nada ahí.")


def _texto(n_chars: int, semilla: str) -> str:
    # Texto no trivial (no todo el mismo carácter), tamaño real de un paso.
    bloque = (semilla + " ") * (n_chars // (len(semilla) + 1) + 1)
    return bloque[:n_chars]


def _paso(i: int) -> dict:
    return {
        "paso": i,
        "faceta": ["jekyll", "hyde", "ada", "kimi", "thot", "hipatia"][i % 6],
        "modelo_real": f"modelo-real-paso-{i}-2026-09",
        "prompt_completo": _texto(CHARS_POR_CAMPO, f"prompt del paso {i} contexto largo de verdad"),
        "salida_completa": _texto(CHARS_POR_CAMPO, f"salida del paso {i} con contenido real generado"),
        "tokens_in": CHARS_POR_CAMPO // 4,
        "tokens_out": CHARS_POR_CAMPO // 4,
        "dependencias": list(range(i)),
        "status": "completed",
    }


_RESULTADO_GRANDE = json.dumps({
    "pipeline_id": BIG_PIPELINE_ID,
    "status": "completed",
    "steps": [_paso(i) for i in range(PASOS_DEL_DETALLE)],
}).encode("utf-8")

print(f"[fake_jacobs] cuerpo del detalle grande: {len(_RESULTADO_GRANDE)} bytes, "
      f"{len(json.loads(_RESULTADO_GRANDE)['steps'])} pasos", file=sys.stderr, flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silencioso: el ruido de decenas de workers no aporta nada al log

    def _credencial_ok(self) -> bool:
        return self.headers.get(ENCABEZADO) == CREDENCIAL_ESPERADA

    def do_GET(self):
        if self.path == "/health":
            self._responder(200, b'{"status":"ok"}')
            return
        if not self._credencial_ok():
            self._responder(401, b'{"detail":"sin credencial"}')
            return
        if self.path == f"/jacobs/pipeline/{BIG_PIPELINE_ID}/results":
            self._responder(200, _RESULTADO_GRANDE)
            return
        if self.path.startswith("/jacobs/pipeline/") and self.path.endswith("/results"):
            # cualquier otro id: resultado chico válido, mismo contrato
            self._responder(200, json.dumps({
                "pipeline_id": "otro", "status": "completed", "steps": [],
            }).encode())
            return
        self._responder(404, b'{"detail":"no declarado en el falso"}')

    def _responder(self, status: int, cuerpo: bytes):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)


def main() -> None:
    # ThreadingHTTPServer: cada conexión en su hilo, soporta concurrencia real
    # (c=25/50/.../200) sin serializar -- lo que sí haría un servidor de un solo hilo.
    servidor = ThreadingHTTPServer(("127.0.0.1", _PUERTO), Handler)
    servidor.daemon_threads = True
    print(f"[fake_jacobs] escuchando en 127.0.0.1:{_PUERTO}", file=sys.stderr, flush=True)
    servidor.serve_forever()


if __name__ == "__main__":
    main()
