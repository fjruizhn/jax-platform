"""Ollama FALSO para la carga del chat web F2-D (2026-10-02).

Sirve, por HTTP real en 127.0.0.1:CARGA_FAKE_OLLAMA_PORT:
    POST /api/chat    -> la respuesta del proveedor que `_call_ollama` espera: un
                         contrato {claim: [], analysis: <N chars>, judgment: null}
                         (el camino "candidato no gobernado", el que SI cruza F2-C
                         y la bandeja F2-D de punta a punta). Responde al instante:
                         lo medido es el costo de la plataforma, no el del modelo.
    POST /api/embed   -> un vector de EMBED_DIM floats (la memoria del chat lo pide
                         al guardar mensajes; sin esto cada guardado fallaria).
    GET  /_stats      -> {"chat": n, "embed": n}: prueba de que las peticiones
                         llegaron de verdad a este proveedor.

El cuerpo de cada POST se lee entero y se descarta (sin parsearlo): el historial
largo viaja en el pedido y hay que consumirlo, pero parsear 300+ KB por llamada
gastaria CPU de la misma maquina y ensuciaria la medicion.

NUNCA escucha en el puerto del Ollama real (11434), ni en 7777/8080.

USO: lo levanta loadtest/chat_f2d_orquestar.py; suelto:
    CARGA_FAKE_OLLAMA_PORT=17434 CARGA_RESPUESTA_CHARS=16000 python3 loadtest/chat_f2d_fake_ollama.py
"""
from __future__ import annotations

import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PUERTOS_PROHIBIDOS = {11434, 7777, 8080}  # Ollama real / LAS MANOS real / jax-platform real
EMBED_DIM = 1024                           # bge-m3, VECTOR(1024) (config JAX_MEMORY_EMBED_DIM)
RESPUESTA_CHARS_POR_DEFECTO = 16_000       # peor caso del brief: 8-16 KB de respuesta


def verificar_puerto(puerto: int) -> None:
    if puerto in PUERTOS_PROHIBIDOS:
        raise SystemExit(
            f"CARGA_FAKE_OLLAMA_PORT={puerto} pisa un puerto REAL {sorted(PUERTOS_PROHIBIDOS)} -- ABORTANDO")


def contenido_de_respuesta(chars: int) -> str:
    """El `message.content` del proveedor: un contrato parseable por
    `_parse_contract_response` sin claims (=> candidato no gobernado)."""
    base = "Respuesta de carga sobre planificacion y presupuesto del trimestre. "
    analysis = (base * (chars // len(base) + 1))[:chars]
    return json.dumps({"claim": [], "analysis": analysis, "judgment": None}, ensure_ascii=False)


_STATS = {"chat": 0, "embed": 0}
_LOCK = threading.Lock()


def _hacer_handler(cuerpo_chat: bytes, cuerpo_embed: bytes):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep-alive: el cliente httpx reutiliza la conexion

        def log_message(self, *_a):  # silencio: 10^4 lineas de log no aportan
            pass

        def _responder(self, cuerpo: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def _consumir_cuerpo(self) -> None:
            restante = int(self.headers.get("Content-Length") or 0)
            while restante > 0:
                trozo = self.rfile.read(min(restante, 1 << 20))
                if not trozo:
                    break
                restante -= len(trozo)

        def do_GET(self):
            if self.path == "/_stats":
                with _LOCK:
                    self._responder(json.dumps(_STATS).encode())
            else:
                self._responder(b"{}")

        def do_POST(self):
            self._consumir_cuerpo()
            if self.path == "/api/chat":
                with _LOCK:
                    _STATS["chat"] += 1
                self._responder(cuerpo_chat)
            elif self.path == "/api/embed":
                with _LOCK:
                    _STATS["embed"] += 1
                self._responder(cuerpo_embed)
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

    return Handler


def main() -> None:
    puerto = int(os.environ.get("CARGA_FAKE_OLLAMA_PORT", "17434"))
    verificar_puerto(puerto)
    chars = int(os.environ.get("CARGA_RESPUESTA_CHARS", str(RESPUESTA_CHARS_POR_DEFECTO)))
    cuerpo_chat = json.dumps({
        "model": "modelo-falso", "done": True,
        "message": {"role": "assistant", "content": contenido_de_respuesta(chars)},
        "prompt_eval_count": 4000, "eval_count": 3000,
    }, ensure_ascii=False).encode("utf-8")
    cuerpo_embed = json.dumps({"embeddings": [[0.001] * EMBED_DIM]}).encode()
    ThreadingHTTPServer.request_queue_size = 256  # antes de instanciar: el bind ya fija el backlog
    servidor = ThreadingHTTPServer(("127.0.0.1", puerto), _hacer_handler(cuerpo_chat, cuerpo_embed))
    servidor.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    servidor.daemon_threads = True
    servidor.serve_forever()


if __name__ == "__main__":
    main()
