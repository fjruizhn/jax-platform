"""Registry of Office verticals (OP 358, pieza 4).

The registry (ids, estados, order) lives in the SERVER, never in the
browser: it decides which rooms can receive projects and in which order
they are announced.  v1 is configuration declared in code; when the
OP 351 manifests stop being drafts this module is the single replacement
point for their ingestion.  The database stores only the project ->
vertical assignment (``office_project_vertical``).
"""
from __future__ import annotations

# Estados posibles de una sala.  Solo ``activa`` acepta proyectos nuevos;
# ``proximamente``/``futura`` se anuncian para dar el mapa completo de la
# firma sin habilitar escritura.
ESTADOS = ("activa", "proximamente", "futura")

# El orden de la tupla es el orden de presentación del hub; no es
# información libre del navegador.
VERTICALES = (
    {"id": "junta", "estado": "proximamente"},
    {"id": "agencia", "estado": "activa"},
    {"id": "bufete", "estado": "proximamente"},
    {"id": "arquitectura", "estado": "proximamente"},
    {"id": "contable", "estado": "proximamente"},
    {"id": "banca", "estado": "proximamente"},
    {"id": "biblioteca", "estado": "futura"},
)
VERTICAL_IDS = frozenset(vertical["id"] for vertical in VERTICALES)
ACTIVA_IDS = frozenset(
    vertical["id"] for vertical in VERTICALES if vertical["estado"] == "activa"
)


def vertical_registrada(vertical: str | None) -> bool:
    """Any registered id is a valid list filter (an honest empty page)."""
    return vertical is not None and vertical in VERTICAL_IDS


def vertical_activa(vertical: str | None) -> bool:
    """Only an active room can receive new projects at creation time."""
    return vertical is not None and vertical in ACTIVA_IDS
