"""Un cupo de operaciones SIMULTANEAS, por usuario y global, en memoria de UN proceso.

Es el mecanismo de `cupo_de_subidas` (E2a, ronda final, MAJOR-4) hecho clase para que lo compartan
dos cupos INDEPENDIENTES: el de subir (`cupo_de_subidas`) y el de reprocesar (`cupo_de_reprocesos`,
jax-platform#186). Cada uno tiene su propio estado: llenar uno nunca quita lugar al otro.

`tomar` y `soltar` no esperan nada entre mirar y sumar, asi que en el loop de asyncio no hay carrera
(ajustes.py exige un solo worker). El llamador suelta en un `finally`.
"""
from __future__ import annotations


class Cupo:
    def __init__(self) -> None:
        self._por_usuario: dict[str, int] = {}
        self._total = 0

    def tomar(self, usuario: str, *, por_usuario: int, globales: int) -> bool:
        """True y el cupo queda tomado; False si el usuario o el servicio ya estan en su tope."""
        if self._total >= globales or self._por_usuario.get(usuario, 0) >= por_usuario:
            return False
        self._total += 1
        self._por_usuario[usuario] = self._por_usuario.get(usuario, 0) + 1
        return True

    def soltar(self, usuario: str) -> None:
        restantes = self._por_usuario.get(usuario, 0) - 1
        if restantes < 0:
            raise RuntimeError(f"cupo: soltar sin tomar ({usuario!r})")
        self._total -= 1
        if restantes:
            self._por_usuario[usuario] = restantes
        else:
            del self._por_usuario[usuario]

    def en_uso(self) -> tuple[int, dict[str, int]]:
        """(total, por usuario) en vuelo, para las pruebas y el diagnostico."""
        return self._total, dict(self._por_usuario)
