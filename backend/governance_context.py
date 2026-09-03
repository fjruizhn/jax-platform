"""
Contexto de gobernanza compartido (REFORMAS Fase 2 SP3).

Antes vivía como shadow_validation._validation_context(). Se mueve acá
porque chat.py también lo necesita (para construir el snapshot del turno,
spec §5.1) y chat.py NO puede importar shadow_validation a nivel de módulo:
shadow_validation importa `from api.chat import ContractResult` -- ciclo.

Config estática cacheada por proceso, con REVALIDACIÓN POR mtime (2026-09-03).

Por qué la revalidación, medido y no supuesto: hasta hoy este módulo cacheaba
con `lru_cache(maxsize=1)` y nada más, así que un cambio en disco solo entraba
al reiniciar el proceso. Eso tenía un modo de falla que no era "config vieja":
si `las_manos/config.toml` se volvía ILEGIBLE con el proceso ya caliente, no
pasaba absolutamente nada visible. El contexto cacheado seguía sirviendo, el
snapshot se construía con hechos que ya no estaban en disco, se inyectaba en el
prompt, y los claims que lo citaban se acreditaban como OBSERVADO. **El snapshot
mentía y ninguna de las dos capas podía notarlo, porque las dos leen este mismo
objeto.** Con la caché fría (primer turno tras un reinicio) el mismo daño era
ruidoso: sha256='ERROR', validated_at NULL y cero veredictos. La revalidación
convierte el caso caliente en el caso frío: fail-closed visible en vez de
silencio.

Cómo: un `stat()` por construcción sobre los TRES archivos que alimentan el
contexto -- medidos con audit hook sobre `open`, no supuestos:

  * `<JAX_REPO>/las_manos/config.toml`  (lo lee load_validation_context en cada
    llamada; es el único que sigue a JAX_REPO)
  * `policy/vocabulary/predicates.yaml` y `closed_vocabulary.yaml`, cuyas rutas
    resuelve `loaders` desde su propio `__file__` y NO siguen a JAX_REPO -- por
    eso se toman de las constantes del módulo, para que no se despeguen si
    alguna cambia de lugar.

El mtime va en la CLAVE de la caché: si cambió, es un miss y se reconstruye; si
no, es el mismo hit de siempre. Costo medido: 2,29 us las tres llamadas a
stat(), contra 9,77 us que cuesta hoy `_build_grounding()` por turno y 2,65 ms
la reconstrucción completa cuando hace falta. No se relee ningún archivo para
decidir. Hash en vez de mtime solo si alguna vez se mide que el mtime miente en
este entorno.

Consecuencia deliberada, y corrige lo que este docstring afirmaba antes: el
spec §9.4 daba por diferida la propiedad de dos capas porque el snapshot y el
resolver leían un objeto congelado, y de ahí que FACT_MISMATCH no pudiera
dispararse por drift. Con revalidación SÍ puede: si la config cambia entre el
turno (donde se arma el snapshot) y la background task (donde se resuelve), el
claim acreditado contra el snapshot viejo se resuelve contra el contexto nuevo
y sale FACT_MISMATCH. Eso es exactamente lo que el spec §4.3 quiere de las dos
capas: que sean independientes.
"""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

JAX_REPO = Path(os.getenv("JAX_REPO_PATH", os.path.expanduser("~/jax")))
if str(JAX_REPO) not in sys.path:
    sys.path.insert(0, str(JAX_REPO))
if str(JAX_REPO / "policy" / "governance") not in sys.path:
    sys.path.insert(0, str(JAX_REPO / "policy" / "governance"))

import loaders as governance_loaders  # noqa: E402
import validator as governance_validator  # noqa: E402


def _source_stamp() -> tuple[tuple[str, int | None], ...]:
    """(ruta, mtime_ns) de los tres archivos fuente. Solo stat(): no abre nada.

    Un archivo que no se puede statear entra como None -- también invalida, y
    la reconstrucción falla ruidosamente, que es el comportamiento buscado
    (P10: ningún gate falla abierto). JAX_REPO se lee en cada llamada a
    propósito: los tests lo reapuntan.
    """
    rutas = (
        JAX_REPO / "las_manos" / "config.toml",
        governance_loaders.PREDICATES_FILE,
        governance_loaders.VOCABULARY_FILE,
    )
    marca: list[tuple[str, int | None]] = []
    for ruta in rutas:
        try:
            marca.append((str(ruta), os.stat(ruta).st_mtime_ns))
        except OSError:
            marca.append((str(ruta), None))
    return tuple(marca)


@lru_cache(maxsize=1)
def _build(stamp: tuple[tuple[str, int | None], ...]):
    """La marca de mtimes es la clave de caché: misma marca, mismo objeto;
    marca distinta, reconstrucción. `stamp` no se usa adentro a propósito."""
    vocabulary = governance_loaders.load_vocabulary()
    ctx = governance_validator.load_validation_context(JAX_REPO, vocabulary.config_paths)
    predicates = governance_loaders.load_predicates()
    return ctx, predicates, vocabulary.term_categories


def validation_context():
    """(ValidationContext, predicates, term_categories) de gobernanza."""
    return _build(_source_stamp())


# Compatibilidad: quien tenía `validation_context.cache_clear()` lo sigue
# teniendo (lo usan los tests para forzar una reconstrucción).
validation_context.cache_clear = _build.cache_clear
