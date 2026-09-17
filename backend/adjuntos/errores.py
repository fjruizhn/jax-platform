"""Códigos estables de los errores de adjuntos (frente D, 2026-09-16).
Cerrados: el frontend los traduce desde t.erroresMesa (es.js/en.js) vía
textoDeErrorDeMesa (frontend/src/api/errores.js). tests/test_adjuntos_i18n_backend.py
exige que cada código esté en es.js y en.js."""
CODIGOS: tuple[str, ...] = (
    "adjunto_demasiado_grande",
    "adjunto_tipo_no_permitido",
    "adjunto_vacio",
    "adjunto_no_encontrado",
    "adjuntos_demasiados",
    "adjuntos_cuota_excedida",
    "adjuntos_sin_espacio",
    "adjuntos_subidas_limite",
    "adjuntos_reintentar",
    "adjuntos_no_soportados",
    "imagen_no_soportada",
    "pdf_ilegible",
    "pdf_sin_texto",
)


class AdjuntoRechazado(Exception):
    def __init__(self, status: int, code: str, **extra):
        if code not in CODIGOS:
            raise ValueError(f"código de adjunto sin traducción declarada: {code!r}")
        super().__init__(code)
        self.status = status
        self.detail = {"code": code, **extra}
