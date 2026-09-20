# Pantalla de Memoria — Plan de implementación

> **Para trabajadores agénticos:** SUB-SKILL REQUERIDA: usar
> `superpowers:subagent-driven-development` (recomendado) o
> `superpowers:executing-plans` para ejecutar este plan tarea por tarea. Los
> pasos usan casillas (`- [ ]`) para seguimiento.

**Goal:** Dar a Fernando una pantalla donde ver la memoria agrupada por tema,
aprobar, corregir y caducar hechos — y a Hyde los mismos endpoints para buscar
y proponer.

**Architecture:** Tres capas, en DOS repos. El esquema y la lógica de datos
viven en `jax` (`jax/memory/`); la API y la pantalla en `jax-platform`. La
plataforma ya importa `MemoryDB` desde el repo jax (`api/chat.py:117`), así que
no se duplica capa de datos: se le agregan los métodos que faltan y se exponen.

**Tech Stack:** Python 3.12 + FastAPI + aiomysql (backend), MariaDB 12.3.3 con
`VECTOR(1024)` e índice HNSW, React 19 + Vite + Tailwind + Zustand +
react-i18next (frontend), pytest y vitest.

**Spec:** `docs/superpowers/specs/2026-09-18-memoria-admin-design.md`
(idéntico a `~/Documents/spec-memoria-2026-09-18.md`).

---

## Global Constraints

Copiados de las políticas del ecosistema y del §4 del spec. Aplican a TODAS las
tareas, sin repetirse en cada una:

- **i18n, cero hardcoding.** Ningún string visible al usuario va en el
  componente. Todo en `frontend/src/i18n/es.js` y `en.js`. Verificar antes de
  cerrar cada tarea de frontend.
- **Dark/light mode siempre.** Colores por variables CSS / tokens de diseño,
  nunca valores literales. Probar en los dos modos.
- **Confirmaciones en ventana propia.** Prohibido `confirm(`, `alert(`,
  `prompt(` — con o sin `window.` delante. Usar `components/Dialogo.jsx`, y
  `ConfirmacionSuma` para lo destructivo (caducar y corregir lo son).
- **Indexing.** Toda consulta que filtre u ordene va contra columna indexada, y
  se verifica con `EXPLAIN` **sobre la consulta real**, no sobre el diseño.
  Buscar `Using filesort` y `Using temporary`. Antecedente de esta casa: un
  índice HNSW existía y un `JOIN` lo anulaba — 58,5 ms contra 0,4 ms.
- **Async.** Nada bloqueante en el camino del usuario. Agrupar por similitud se
  diseña para 10.000 hechos, no para 116.
- **Carga.** Sin número medido, no hay GO. El número se escribe con fecha en la
  Biblioteca del proyecto.
- **Escritura de configuración.** Si alguna tarea necesitara escribir
  `axioma_config`, el único camino es `config_audit.escribir`; el detector
  `backend/tests/test_config_audit.py::test_ningun_otro_modulo_escribe_axioma_config`
  lo hace cumplir.
- **El que produce no aprueba.** No existe aprobación automática en ninguna
  tarea. Un hecho lo verifica una persona, nunca el sistema ni Hyde.
- **Entorno de tests.** El `.env` se lee con `set -a; . <(sudo -n cat /etc/jax/.env); set +a`.
  Los tests corren contra `jax_memory_test`, NUNCA contra `jax_memory`.

---

## Estructura de archivos

**Repo `jax`** (esquema y datos):

| Archivo | Responsabilidad |
|---|---|
| `jax_memory_schema.sql` | describe el esquema; fuente de verdad del checker de deriva |
| `jax/memory/migrations.py` | lleva un esquema existente hacia adelante (`_COLUMNAS`, `_INDICES`) |
| `jax/memory/db.py` | métodos de datos: `verify_fact`, `supersede_fact`, `get_facts`, nuevo `expire_fact` |
| `tests/test_memoria_gobernanza.py` | **nuevo** — autoría, caducidad, y que `expires_at` se respete |

**Repo `jax-platform`** (API y pantalla):

| Archivo | Responsabilidad |
|---|---|
| `backend/api/admin/memoria.py` | **nuevo** — los endpoints, uno por verbo del spec |
| `backend/api/admin/__init__.py` | exporta `memoria_router` |
| `backend/main.py:235-255` | monta el router en `ROUTERS` |
| `backend/tests/test_memoria_api.py` | **nuevo** — contrato de los endpoints, propiedad y rol |
| `backend/tests/test_memoria_indices.py` | **nuevo** — `EXPLAIN` sobre la consulta real |
| `frontend/src/pages/Memoria.jsx` | **nuevo** — la pantalla |
| `frontend/src/pages/Memoria.test.jsx` | **nuevo** |
| `frontend/src/components/Memoria/GrupoDeHechos.jsx` | **nuevo** — un grupo, con su lote |
| `frontend/src/components/Memoria/FichaDeHecho.jsx` | **nuevo** — un hecho con su procedencia |
| `frontend/src/i18n/es.js`, `en.js` | textos |

---

### Task 1 (repo `jax`): las columnas de autoría que el spec necesita y no existen

**Por qué primero.** El spec pide aprobar «con **quién** y cuándo» y corregir
registrando «quién la corrigió». Medido el 2026-09-20 contra producción: `facts`
tiene `verified_at` pero **no tiene `verified_by`**, y `superseded_by` es el id
del **hecho** que reemplaza, no del usuario. Sin estas dos columnas, el «quién»
no se puede guardar — y aprobar sin dueño es otro sello vacío, que es justo el
problema que la pantalla viene a resolver.

**Files:**
- Modify: `jax_memory_schema.sql` (bloque `CREATE TABLE facts`)
- Modify: `jax/memory/migrations.py` (lista `_COLUMNAS`)
- Test: `tests/test_memoria_gobernanza.py` (crear)

**Interfaces:**
- Consumes: nada.
- Produces: columnas `facts.verified_by INT NULL`, `facts.superseded_by_user INT NULL`,
  índice `idx_facts_revision (is_verified, expires_at, created_at)`.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_memoria_gobernanza.py`:

```python
# tests/test_memoria_gobernanza.py
"""Gobernanza de la memoria: autoría de la aprobación y de la corrección.

Medido el 2026-09-20 contra produccion: `facts` tiene `verified_at` pero NO
`verified_by`, y `superseded_by` es el id del HECHO que reemplaza, no del
usuario. El spec 2026-09-18-memoria-admin §2.2 pide aprobar "con quién y
cuándo" -- sin estas columnas el quién no se puede guardar.
"""
import pytest

from jax.memory import migrations

COLUMNAS_NUEVAS = {"verified_by", "superseded_by_user"}


def test_las_columnas_de_autoria_estan_declaradas_en_el_migrador():
    declaradas = {col for (tabla, col, _ddl) in migrations._COLUMNAS if tabla == "facts"}
    assert COLUMNAS_NUEVAS <= declaradas, f"faltan: {COLUMNAS_NUEVAS - declaradas}"


def test_el_indice_de_revision_esta_declarado():
    """La pantalla filtra por verificado y caducidad y ordena por fecha: las
    tres columnas van en un indice compuesto, o el EXPLAIN de la Task 3 falla."""
    indices = {nombre for (tabla, nombre, _ddl) in migrations._INDICES if tabla == "facts"}
    assert "idx_facts_revision" in indices


def test_el_esquema_declarado_y_el_migrador_no_se_contradicen():
    """jax_memory_schema.sql es la fuente de verdad (checker de deriva) y
    migrations.py lleva las bases existentes hacia adelante. Si una columna
    esta en uno y no en el otro, una base nueva y una vieja quedan distintas --
    que es exactamente como `depends_on` de jacobs_steps termino existiendo
    solo en produccion (DEUDA.md)."""
    import pathlib
    esquema = pathlib.Path(__file__).resolve().parent.parent / "jax_memory_schema.sql"
    texto = esquema.read_text(encoding="utf-8")
    for col in COLUMNAS_NUEVAS:
        assert col in texto, f"{col} no esta en jax_memory_schema.sql"
```

- [ ] **Step 2: Correr el test y verlo fallar**

```bash
cd <worktree-jax>
set -a; . <(sudo -n cat /etc/jax/.env); set +a
export PYTHONPATH=.:las_manos
python -m pytest tests/test_memoria_gobernanza.py -v
```

Esperado: **FAIL** con `faltan: {'verified_by', 'superseded_by_user'}` en el
primer test, y `AssertionError: idx_facts_revision` en el segundo. Si algún test
pasa ya, parar: la columna existe y este plan está desactualizado.

- [ ] **Step 3: Implementar — el migrador**

En `jax/memory/migrations.py`, agregar a `_COLUMNAS`:

```python
    # Autoria de la aprobacion (spec 2026-09-18-memoria-admin §2.2: "con quien y
    # cuando"). `verified_at` ya existia; el quien, no. NULL para las filas
    # viejas: no se inventa un aprobador retroactivo -- el unico hecho
    # verificado de antes de esta ronda queda con autor desconocido, y eso es
    # la verdad.
    ("facts", "verified_by",
     "ALTER TABLE facts ADD COLUMN verified_by INT NULL"),
    # Quien CORRIGIO. Distinto de `superseded_by`, que es el id del HECHO que
    # reemplaza. Los dos hacen falta: uno reconstruye la cadena de versiones, el
    # otro dice de quien fue la decision.
    ("facts", "superseded_by_user",
     "ALTER TABLE facts ADD COLUMN superseded_by_user INT NULL"),
```

Y a `_INDICES`:

```python
    # La pantalla de Memoria filtra por verificado y por caducidad, y ordena por
    # fecha. Compuesto en ese orden: los dos predicados de igualdad/rango
    # primero, el ORDER BY al final, para que no aparezca `Using filesort`.
    ("facts", "idx_facts_revision",
     "CREATE INDEX idx_facts_revision ON facts (is_verified, expires_at, created_at)"),
```

- [ ] **Step 4: Implementar — el esquema declarado**

En `jax_memory_schema.sql`, dentro de `CREATE TABLE facts`, después de
`verified_at`:

```sql
  verified_by INT NULL,
  superseded_by_user INT NULL,
```

y al final de la tabla:

```sql
  KEY idx_facts_revision (is_verified, expires_at, created_at),
```

- [ ] **Step 5: Correr el test y verlo pasar**

```bash
python -m pytest tests/test_memoria_gobernanza.py -v
```

Esperado: **3 passed**.

- [ ] **Step 6: Verificar la migración contra una base de verdad**

```bash
python -m pytest tests/ -q -k "memoria or migrations"
```

Y comprobar a mano que es idempotente — correrla dos veces no debe fallar:

```bash
python -c "
import asyncio, os
from jax.memory.db import MemoryDB
async def m():
    db = MemoryDB(); await db.connect()
    from jax.memory import migrations
    print('1ra:', await migrations.ensure_schema(db.pool))
    print('2da:', await migrations.ensure_schema(db.pool))
asyncio.run(m())"
```

Esperado: `1ra: True` y `2da: True`, sin excepción.

- [ ] **Step 7: Commit**

```bash
git add jax_memory_schema.sql jax/memory/migrations.py tests/test_memoria_gobernanza.py
git commit -m "feat(memoria): columnas de autoria de aprobacion y correccion"
```

---

### Task 2 (repo `jax`): que la caducidad se respete y la autoría se guarde

**Por qué.** `expires_at` existe en la tabla desde siempre y **ningún código la
lee** — verificado por búsqueda en `jax/memory/` el 2026-09-20: cero
coincidencias. Una VERDAD OPERACIONAL de hace dos semanas pesa hoy igual que la
de esta mañana. Y `verify_fact(fact_id)` no recibe quién aprueba.

**Files:**
- Modify: `jax/memory/db.py:1370` (`get_facts`), `:1441` (`verify_fact`), `:823` (`supersede_fact`)
- Test: `tests/test_memoria_gobernanza.py` (agregar)

**Interfaces:**
- Consumes: columnas de la Task 1.
- Produces:
  - `async def verify_fact(self, fact_id: int, verified_by: int) -> Optional[bool]`
  - `async def supersede_fact(self, old_fact_id: int, new_fact_id: int, superseded_by_user: int) -> Optional[bool]`
  - `async def expire_fact(self, fact_id: int, expires_at: datetime | None) -> Optional[bool]`
  - `get_facts(..., incluir_vencidos: bool = False)` — por defecto **excluye** vencidos.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `tests/test_memoria_gobernanza.py`:

```python
import inspect
from jax.memory.db import MemoryDB


def test_verify_fact_exige_saber_quien_aprueba():
    """Sin el quien, aprobar es un sello sin dueno -- el problema que la
    pantalla viene a resolver, no a repetir."""
    firma = inspect.signature(MemoryDB.verify_fact)
    assert "verified_by" in firma.parameters
    assert firma.parameters["verified_by"].default is inspect.Parameter.empty, \
        "verified_by no puede tener default: un aprobador implicito es un aprobador inventado"


def test_supersede_fact_registra_quien_corrigio():
    firma = inspect.signature(MemoryDB.supersede_fact)
    assert "superseded_by_user" in firma.parameters
    assert firma.parameters["superseded_by_user"].default is inspect.Parameter.empty


def test_existe_expire_fact():
    assert hasattr(MemoryDB, "expire_fact")
    firma = inspect.signature(MemoryDB.expire_fact)
    assert {"fact_id", "expires_at"} <= set(firma.parameters)


def test_get_facts_excluye_vencidos_por_defecto():
    """El spec §2.4: un hecho vencido no se borra, deja de pesar en la busqueda.
    Hoy `expires_at` no la lee NADIE (verificado 2026-09-20)."""
    fuente = inspect.getsource(MemoryDB.get_facts)
    assert "expires_at" in fuente, "get_facts sigue sin mirar la caducidad"
    firma = inspect.signature(MemoryDB.get_facts)
    assert firma.parameters["incluir_vencidos"].default is False
```

- [ ] **Step 2: Correr y ver fallar**

```bash
python -m pytest tests/test_memoria_gobernanza.py -v -k "verify or supersede or expire or vencidos"
```

Esperado: **4 FAIL** — `verified_by` no está en la firma, `expire_fact` no
existe, `get_facts` no menciona `expires_at`.

- [ ] **Step 3: Implementar `verify_fact`**

En `jax/memory/db.py:1441`, reemplazar la firma y el UPDATE:

```python
    async def verify_fact(self, fact_id: int, verified_by: int) -> Optional[bool]:
        """Marca un fact como verificado, con QUIEN y cuando. `confidence` NO se
        toca (es ortogonal: confidence = certeza del extractor, is_verified =
        validacion de una persona).

        `verified_by` NO tiene default a proposito: un aprobador implicito es un
        aprobador inventado, y el punto de esta columna es que la aprobacion
        tenga dueno (spec 2026-09-18-memoria-admin §2.2)."""
        if not self.pool:
            return None
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                affected = await cur.execute(
                    "UPDATE facts SET is_verified = TRUE, verified_at = NOW(), "
                    "verified_by = %s WHERE id = %s",
                    (verified_by, fact_id),
                )
            await conn.commit()
        return affected > 0
```

- [ ] **Step 4: Implementar `supersede_fact`**

En `jax/memory/db.py:823`:

```python
    async def supersede_fact(self, old_fact_id: int, new_fact_id: int,
                             superseded_by_user: int) -> Optional[bool]:
        """Marca old_fact_id como reemplazado por new_fact_id, y registra quien
        lo decidio. No borra nada: la historia de una correccion queda
        reconstruible.

        OJO: este metodo tiene DOS llamadores con naturaleza distinta. El
        automatico (`save_fact`, linea ~975, cuando detecta un casi-duplicado) y
        el humano (la pantalla de Memoria). Para el automatico, el
        `superseded_by_user` es el id del usuario cuya sesion produjo el hecho
        nuevo -- nunca 0 ni None: si no se sabe quien, no se supersede."""
        if not self.pool:
            return None
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                affected = await cur.execute(
                    "UPDATE facts SET superseded_by = %s, superseded_at = NOW(), "
                    "superseded_by_user = %s WHERE id = %s",
                    (new_fact_id, superseded_by_user, old_fact_id),
                )
            await conn.commit()
        return affected > 0
```

Y actualizar el llamador automático en `save_fact` (línea ~975) para pasar el
`user_id` que ya recibe:

```python
                if await self.supersede_fact(candidate["id"], fact_id, user_id):
```

- [ ] **Step 5: Implementar `expire_fact`**

Agregar junto a `verify_fact`:

```python
    async def expire_fact(self, fact_id: int, expires_at) -> Optional[bool]:
        """Pone (o quita, con None) la fecha de vencimiento de un hecho.

        Caducar NO es borrar: el hecho sigue, deja de pesar en la busqueda y se
        ve como vencido (spec §2.4). Por eso no hay `delete` en esta pantalla:
        borrar es perder la historia de lo que creimos."""
        if not self.pool:
            return None
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                affected = await cur.execute(
                    "UPDATE facts SET expires_at = %s WHERE id = %s",
                    (expires_at, fact_id),
                )
            await conn.commit()
        return affected > 0
```

- [ ] **Step 6: Implementar el filtro de vencidos en `get_facts`**

En `jax/memory/db.py:1370`, agregar el parámetro y el predicado:

```python
    async def get_facts(self, only_unverified: bool = True,
                        only_verified: bool = False,
                        fact_type: Optional[str] = None,
                        limit: int = 20,
                        user_id: Optional[int] = None,
                        project_id: Optional[int] = None,
                        incluir_vencidos: bool = False) -> Optional[list]:
```

y dentro, junto a los demás `WHERE`:

```python
        # Spec §2.4: un hecho vencido deja de pesar. `expires_at IS NULL` es
        # "no caduca" y tiene que seguir entrando -- un `expires_at < NOW()` a
        # secas los dejaria a TODOS afuera, que es el error clasico con NULL.
        if not incluir_vencidos:
            condiciones.append("(expires_at IS NULL OR expires_at > NOW())")
```

- [ ] **Step 7: Correr y ver pasar**

```bash
python -m pytest tests/test_memoria_gobernanza.py -v
```

Esperado: **7 passed**.

- [ ] **Step 8: Correr la suite completa y comparar contra master**

```bash
python -m pytest -q -p no:randomly --ignore=tests/policy 2>&1 | tail -3
```

Comparar el número con el de `origin/master` corrido con el MISMO comando. El
delta tiene que ser exactamente el número de tests agregados. Si hay un fallo
nuevo, es tuyo: `supersede_fact` cambió de aridad y puede haber llamadores.

- [ ] **Step 9: Commit**

```bash
git add jax/memory/db.py tests/test_memoria_gobernanza.py
git commit -m "feat(memoria): caducidad respetada y autoria de aprobacion/correccion"
```

---

### Task 3 (repo `jax-platform`): API de lectura, con el EXPLAIN verificado

**Files:**
- Create: `backend/api/admin/memoria.py`
- Modify: `backend/api/admin/__init__.py`, `backend/main.py:235-255`
- Test: `backend/tests/test_memoria_api.py`, `backend/tests/test_memoria_indices.py`

**Interfaces:**
- Consumes: `MemoryDB.get_facts(..., incluir_vencidos=False)` de la Task 2.
- Produces: `GET /api/admin/memoria/hechos` → `{"hechos": [...], "total": int}`.
  Cada hecho: `{id, texto, tipo, confianza, verificado, verificado_por,
  verificado_at, vence_at, vencido, creado_at, procedencia: {mensaje_id, faceta}}`.

- [ ] **Step 1: Escribir el test del contrato**

```python
# backend/tests/test_memoria_api.py
"""Contrato de los endpoints de memoria. La pantalla es de superadmin: la
memoria es lo unico que el sistema acumula sobre nosotros."""


def test_listar_hechos_exige_superadmin(client):
    r = client.get("/api/admin/memoria/hechos")
    assert r.status_code == 401


def test_listar_hechos_devuelve_procedencia_siempre(client_superadmin):
    """«La memoria sin procedencia es otra forma de suposicion» -- spec §2.1.
    Un hecho sin de-donde-salio no se muestra: se muestra con la procedencia
    vacia y marcada, nunca sin el campo."""
    r = client_superadmin.get("/api/admin/memoria/hechos?limite=5")
    assert r.status_code == 200
    for hecho in r.json()["hechos"]:
        assert "procedencia" in hecho
        assert {"mensaje_id", "faceta"} <= set(hecho["procedencia"])


def test_los_vencidos_no_salen_salvo_que_se_pidan(client_superadmin):
    sin = client_superadmin.get("/api/admin/memoria/hechos?limite=200").json()["hechos"]
    con = client_superadmin.get("/api/admin/memoria/hechos?limite=200&incluir_vencidos=true").json()["hechos"]
    assert len(con) >= len(sin)
    assert all(not h["vencido"] for h in sin)
```

- [ ] **Step 2: Correr y ver fallar**

```bash
cd backend
set -a; . <(sudo -n cat /etc/jax/.env); set +a
python -m pytest tests/test_memoria_api.py -v
```

Esperado: **404** en todos (el router no existe).

- [ ] **Step 3: Implementar el router**

Crear `backend/api/admin/memoria.py` siguiendo el patrón de
`backend/api/admin/config_admin.py` (mismos imports de `require_superadmin`,
`rate_limit`, `get_pool`). El acceso a `MemoryDB` se hace reusando el helper de
`api/chat.py` — **no** se duplica el import con `sys.path.insert`.

- [ ] **Step 4: Registrar el router**

En `backend/api/admin/__init__.py` exportar `memoria_router`, y agregarlo a la
tupla `ROUTERS` de `backend/main.py:235-255`, después de `ejecutor_router`.

- [ ] **Step 5: Correr y ver pasar**

```bash
python -m pytest tests/test_memoria_api.py -v
```

- [ ] **Step 6: El test de índice — EXPLAIN sobre la consulta REAL**

```python
# backend/tests/test_memoria_indices.py
"""Un indice que existe no es un indice que se usa. Antecedente medido en esta
casa (2026-09-11, jax_memory): el HNSW de `messages` existia y la busqueda
semantica NO lo usaba por un JOIN -- 58,5 ms contra 0,4 ms con 1.149 filas.

Este test mira el EXPLAIN de la consulta que el endpoint corre DE VERDAD, no
una parecida escrita a mano: se la pide al modulo."""
from api.admin import memoria


def test_el_listado_usa_el_indice_y_no_ordena_en_memoria(client_superadmin):
    plan = client_superadmin.portal.call(_explain, memoria.SQL_LISTAR, memoria.ARGS_EJEMPLO)
    texto = " ".join(str(c) for fila in plan for c in fila)
    assert "idx_facts_revision" in texto, f"no usa el indice: {texto}"
    assert "Using filesort" not in texto, f"ordena en memoria: {texto}"
    assert "Using temporary" not in texto, f"tabla temporal: {texto}"
```

- [ ] **Step 7: Correr el EXPLAIN y arreglar la consulta hasta que pase**

Si aparece `Using filesort`, el `ORDER BY` no está cubierto por
`idx_facts_revision`: revisar el orden de las columnas del índice contra el
orden de los predicados. **No** agregar un índice nuevo sin medir antes.

- [ ] **Step 8: Commit**

```bash
git add backend/api/admin/memoria.py backend/api/admin/__init__.py backend/main.py \
        backend/tests/test_memoria_api.py backend/tests/test_memoria_indices.py
git commit -m "feat(memoria): API de lectura de hechos, con el indice verificado por EXPLAIN"
```

---

### Task 4 (repo `jax-platform`): aprobar, corregir y caducar — con auditoría

**Files:**
- Modify: `backend/api/admin/memoria.py`
- Test: `backend/tests/test_memoria_api.py` (agregar)

**Interfaces:**
- Consumes: `verify_fact(fact_id, verified_by)`, `supersede_fact(old, new, by)`,
  `expire_fact(fact_id, expires_at)` de la Task 2.
- Produces:
  - `POST /api/admin/memoria/hechos/aprobar` con `{"ids": [int]}` → `{"aprobados": int}`
  - `POST /api/admin/memoria/hechos/{id}/corregir` con `{"texto": str}` → `{"nuevo_id": int}`
  - `POST /api/admin/memoria/hechos/{id}/caducar` con `{"vence_at": str|null}`

- [ ] **Step 1: Escribir los tests**

```python
def test_aprobar_en_lote_registra_quien(client_superadmin):
    """Spec §2.2: aprobar en lote, porque revisar 115 de a uno no lo hace
    nadie -- y una funcion que nadie usa es igual a no tenerla."""
    ids = [h["id"] for h in client_superadmin.get(
        "/api/admin/memoria/hechos?verificado=false&limite=3").json()["hechos"]]
    r = client_superadmin.post("/api/admin/memoria/hechos/aprobar", json={"ids": ids})
    assert r.status_code == 200 and r.json()["aprobados"] == len(ids)
    for h in client_superadmin.get("/api/admin/memoria/hechos?limite=200").json()["hechos"]:
        if h["id"] in ids:
            assert h["verificado"] is True
            assert h["verificado_por"] is not None, "aprobado sin dueno"


def test_corregir_no_borra_marca_como_superado(client_superadmin):
    """Spec §2.3 y el Protocolo de la Memoria Viva: una memoria falsa no se
    borra en silencio, se marca como corregida, con version nueva."""
    viejo = client_superadmin.get("/api/admin/memoria/hechos?limite=1").json()["hechos"][0]
    r = client_superadmin.post(f"/api/admin/memoria/hechos/{viejo['id']}/corregir",
                               json={"texto": "version corregida de prueba"})
    assert r.status_code == 200
    nuevo_id = r.json()["nuevo_id"]
    todos = client_superadmin.get(
        "/api/admin/memoria/hechos?limite=500&incluir_superados=true").json()["hechos"]
    por_id = {h["id"]: h for h in todos}
    assert por_id[viejo["id"]]["superado_por"] == nuevo_id
    assert viejo["id"] in por_id, "el hecho viejo desaparecio: eso es borrar"


def test_no_existe_endpoint_de_borrado(client_superadmin):
    """Fuera de alcance por decision del spec §5: borrar es perder la historia
    de lo que creimos. Este test ata esa decision."""
    r = client_superadmin.delete("/api/admin/memoria/hechos/1")
    assert r.status_code in (404, 405)


def test_nadie_puede_aprobar_automaticamente(client_superadmin):
    """Spec §2.2: no hay aprobacion automatica. Si el sistema se aprueba a si
    mismo, is_verified deja de significar algo."""
    import inspect
    from api.admin import memoria
    fuente = inspect.getsource(memoria)
    assert "auto_aprobar" not in fuente and "aprobar_todo" not in fuente
```

- [ ] **Step 2: Correr y ver fallar** — `404` en los tres endpoints.

- [ ] **Step 3: Implementar los tres endpoints.** Cada escritura toma el
  `user.user_id` de `require_superadmin` y lo pasa como autor. La corrección va
  en **una sola transacción**: crear el hecho nuevo y marcar el viejo como
  superado, o ninguna de las dos. Antecedente: `jax-platform#107`, un evento de
  cierre fuera de la transacción del turno fue una carrera real.

- [ ] **Step 4: Correr y ver pasar.**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(memoria): aprobar en lote, corregir y caducar, con autoria"
```

---

### Task 5 (repo `jax-platform`): agrupar por tema, sin bloquear el request

**Files:**
- Modify: `backend/api/admin/memoria.py`
- Test: `backend/tests/test_memoria_grupos.py` (crear)

**Interfaces:**
- Produces: `GET /api/admin/memoria/grupos` →
  `{"grupos": [{"tema": str, "hechos": [int], "sin_verificar": int, "casi_duplicados": [[int]]}]}`

- [ ] **Step 1: Escribir el test**

```python
def test_los_casi_duplicados_salen_juntos_y_marcados(client_superadmin):
    """Spec §2.1: "estos tres dicen lo mismo". Los hechos 136, 138 y 139 dicen
    lo mismo -- «JAX no tiene capacidad nativa para SQL»-- con tres redacciones
    distintas, del 4 de septiembre, y HOY SON FALSOS. Si el agrupamiento no los
    junta, no sirve."""
    grupos = client_superadmin.get("/api/admin/memoria/grupos").json()["grupos"]
    juntos = [g for g in grupos if {136, 138, 139} <= set(g["hechos"])]
    assert juntos, "los tres hechos de SQL quedaron en grupos distintos"
    assert any({136, 138, 139} <= set(d) for d in juntos[0]["casi_duplicados"])


def test_agrupar_no_bloquea_el_event_loop(client_superadmin):
    """Spec §4: agrupar 116 hechos por similitud no puede bloquear el request.
    Con 116 es trivial; con 10.000 no. Se disena para el segundo caso."""
    import inspect
    from api.admin import memoria
    fuente = inspect.getsource(memoria.agrupar_por_tema)
    assert "await" in fuente
    for bloqueante in ("time.sleep", "requests.", "subprocess.run"):
        assert bloqueante not in fuente
```

- [ ] **Step 2: Correr y ver fallar.**

- [ ] **Step 3: Implementar.** El agrupamiento usa el índice vectorial de
  `embedding_bge_m3` — **verificar con EXPLAIN que lo usa**, porque el
  antecedente de esta casa es que un `JOIN` de más lo descarta en silencio. El
  cálculo pesado va en `asyncio.to_thread` o en una tarea de fondo con caché
  invalidado en la misma transacción que escribe un hecho.

- [ ] **Step 4: Correr y ver pasar. Step 5: Commit.**

---

### Task 6 (repo `jax-platform`): la pantalla

**Files:**
- Create: `frontend/src/pages/Memoria.jsx`, `frontend/src/pages/Memoria.test.jsx`,
  `frontend/src/components/Memoria/GrupoDeHechos.jsx`,
  `frontend/src/components/Memoria/FichaDeHecho.jsx`
- Modify: `frontend/src/i18n/es.js`, `frontend/src/i18n/en.js`, el router de admin

- [ ] **Step 1: Escribir los tests de la pantalla**

```jsx
it('cada hecho muestra su procedencia', async () => {
  render(<Memoria />)
  const ficha = await screen.findByTestId('hecho-136')
  expect(within(ficha).getByText(es.memoria.procedencia.mensaje)).toBeInTheDocument()
  expect(within(ficha).getByText(es.memoria.procedencia.faceta)).toBeInTheDocument()
})

it('caducar pide confirmacion en ventana propia, no del navegador', async () => {
  const confirmSpy = vi.spyOn(window, 'confirm')
  render(<Memoria />)
  await userEvent.click(await screen.findByRole('button', { name: es.memoria.caducar }))
  expect(await screen.findByRole('dialog')).toBeInTheDocument()
  expect(confirmSpy).not.toHaveBeenCalled()
})

it('ningun texto visible esta hardcodeado', () => {
  const fuente = readFileSync('src/pages/Memoria.jsx', 'utf8')
  expect(fuente).not.toMatch(/>[A-ZÁÉÍÓÚÑ][a-záéíóúñ ]{3,}</)
})
```

- [ ] **Step 2: Correr y ver fallar. Step 3: Implementar. Step 4: Ver pasar.**

- [ ] **Step 5: Verificar las políticas absolutas a mano**

```bash
grep -nE "(^|[^.])\b(confirm|alert|prompt)\(" frontend/src/pages/Memoria.jsx frontend/src/components/Memoria/*.jsx
```

Esperado: **sin salida**. Ojo: el patrón busca la forma DESNUDA además de
`window.` — un escaneo que solo mire `window.confirm` da verde con el defecto
presente.

Y abrir la pantalla en tema claro y oscuro antes de cerrar la tarea.

- [ ] **Step 6: Commit.**

---

### Task 7: la prueba de carga, con el peor caso

**Por qué es una tarea y no un paso.** Regla 4 del rendimiento: ningún servicio
ni pantalla llega a producción sin una prueba de carga. **Sin número medido, no
hay GO** — «debería aguantar» es una suposición, y las suposiciones sobre carga
se pagan con la app caída delante del cliente.

- [ ] **Step 1: Sembrar el peor caso** — no los 116 hechos de hoy: 10.000, con
  el grupo más grande y el usuario con más hechos.
- [ ] **Step 2: Medir** `GET /api/admin/memoria/grupos` y `…/hechos` con
  concurrencia real. Registrar peticiones por segundo, **latencia p95**, y con
  cuántos usuarios simultáneos empieza a degradarse.
- [ ] **Step 3: Verificar que se mide el servicio, no un literal.** Antecedente
  del 2026-09-16: un p95 de 2,99 ms medía FastAPI devolviendo un literal, no el
  servicio — y hubo que volver a medir.
- [ ] **Step 4: Escribir el número en la Biblioteca del proyecto, con fecha.**
- [ ] **Step 5: Commit.**

---

## Cómo se sabe que funcionó (del spec §6)

1. Fernando abre la pantalla y **en un rato deja los 116 hechos revisados**. Si
   revisar 116 se siente imposible, la pantalla está mal hecha.
2. Los tres hechos falsos sobre SQL (136, 138, 139) quedan **corregidos y
   superados**, no borrados.
3. Hyde puede buscar en la memoria desde una sesión nueva y encontrar algo que
   se aprendió hoy.
4. Hay un número de carga medido, con fecha, en la Biblioteca.

## Fuera de alcance (del spec §5) — no ampliar sin decisión de Fernando

Aprobación automática · borrar hechos · cambiar el extractor · el escritor del
Ejecutor (que cada misión deje su lección: depende de esta pantalla, va en la
ronda siguiente).

En memoria de Jairo Urbina.
