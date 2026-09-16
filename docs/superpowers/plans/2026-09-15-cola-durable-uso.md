# Plan: cola durable con reintento para el registro de uso

**Pedido de Fernando (2026-09-15, chat):** "termina el pendiente del 29" — el pendiente con fecha 2026-09-29 que dejó la Task 7 de la tanda anterior.
**Base:** master `ae28293`. **Rama:** `feat/cola-durable-uso`. **Worktree:** `/home/fruiz/worktrees/jax-platform-cola-uso`.

## El problema, medido
`record_usage` (`backend/api/admin/usage.py:79`) escribe una fila en `axioma_usage` por cada turno de chat y por cada imagen. Si esa escritura falla, el `except` es fail-soft a propósito: el turno ya se cobró al proveedor y ya se le respondió al usuario, así que un 500 no recupera nada y sí le quita la respuesta. Hoy la pérdida solo se **cuenta** (`registros_perdidos`, contador en memoria desde el arranque) y se loguea. La fila se pierde para siempre, y el total de Admin → Costos queda por debajo del gasto real.

Se llama en línea, con `await`, dentro del request: `api/chat.py:1104` y `api/image.py:106`.

## La decisión de diseño
Un **archivo de respaldo propio** (una línea JSON por fila pendiente) y una **tarea de fondo que reintenta**. No una tabla: la base es justamente lo que puede estar caído.

- **Dónde:** `JAX_USAGE_SPOOL_PATH` en el entorno, con default `/srv/jax-data/usage-spool/pendientes.jsonl`. El servicio corre como `fruiz` y `/srv/jax-data` es suyo, con 1,8 T libres. Sin hardcoding: el default vive en el código, el valor real en `.env`.
  - Ese archivo NO está respaldado: es tránsito, no archivo histórico. Se vacía solo cuando las filas entran a la base.
- **Idempotencia:** `axioma_usage` suma una columna `spool_id CHAR(36) NULL` con índice **UNIQUE**. Cada fila que va al respaldo lleva su id. El reintento inserta con `INSERT IGNORE` (o `ON DUPLICATE KEY UPDATE id=id`): si el proceso se muere entre el INSERT y el borrado de la línea, el reintento siguiente no duplica el cobro.
  - Las filas normales (camino feliz) siguen con `spool_id` NULL, y un índice UNIQUE admite varios NULL en MariaDB.
- **La hora es la del turno, no la del reintento:** la fila guardada lleva su `created_at` original y el INSERT lo escribe explícito. Si no, una caída de dos horas movería el costo al día siguiente.
- **Cota dura:** el respaldo tiene tope (`JAX_USAGE_SPOOL_MAX_FILAS`, default 50.000). Pasado el tope se descarta la fila MÁS VIEJA y se cuenta aparte (`perdidas_por_desborde`). Una caída larga no puede llenar el disco, y la pérdida sigue siendo visible.
- **Un solo proceso escribe:** el servicio corre un único uvicorn (verificado: `ExecStart` sin `--workers`). Aun así, el módulo serializa con un `asyncio.Lock`, porque los turnos concurrentes sí compiten entre sí.

## Global Constraints
- **Barrera de DB:** `/etc/jax/.env` es PRODUCCIÓN. Solo pytest toca la DB, y el conftest fuerza `jax_memory_test`.
- **Git:** nunca `git stash`; los archivos se agregan uno por uno; nunca `.impeccable/`; no se hace push desde las tareas; solo se trabaja en este worktree.
- **TDD:** cada test se ve rojo antes de pasar a verde.
- **Nada bloqueante dentro de `async`:** el archivo se escribe con `asyncio.to_thread`, nunca con `open()` directo en el loop.
- **Fail-soft con marca:** todo `except` amplio que no relanza lleva `# fail-soft: <razón>`.
- **Pisos:** exactos, medidos y con comentario fechado.
- **Frontend:** tokens del tema, i18n es/en simétrico, sin texto hardcodeado.
- **Carga:** gate de merge (U29). `record_usage` está en el camino caliente del chat.
- **Migración:** todo índice nuevo se construye con `ALGORITHM=INPLACE, LOCK=NONE` y `lock_wait_timeout` acotado; si vence, ERROR y reintento en el próximo arranque.

## Task 1 — El respaldo: escribir y leer filas pendientes
**Archivos:** `backend/uso/cola.py` (nuevo) y su test.

1. Módulo puro, sin FastAPI y sin la base:
   - `ruta_del_respaldo()` lee `JAX_USAGE_SPOOL_PATH` o devuelve el default; crea el directorio si falta.
   - `async def encolar(fila: dict) -> bool`: agrega una línea JSON (`spool_id`, `created_at` ISO, y las 8 columnas), hace `flush` + `fsync`, y devuelve si pudo.
   - `async def leer_pendientes(limite) -> list[dict]`: lee las primeras N líneas válidas.
   - `async def quitar(spool_ids: set[str]) -> int`: reescribe el archivo sin esas filas, de forma atómica (archivo temporal + `os.replace` en el mismo directorio).
   - `def profundidad() -> int` y `estadisticas() -> dict` (`en_cola`, `perdidas_por_desborde`, `ultimo_error_de_respaldo`).
   - Todo el I/O va por `asyncio.to_thread`, y las operaciones se serializan con un `asyncio.Lock` del módulo.
2. **Una línea corrupta no rompe la cola:** se saltea, se cuenta y se loguea; las demás siguen. Un archivo que no se puede leer entero es un error visible, no un silencio.
3. **Tope:** al encolar por encima del máximo, se descarta la más vieja y sube `perdidas_por_desborde`.
4. Tests, cada uno rojo primero: encolar y leer; reescritura atómica; una línea corrupta en el medio; el tope; el `fsync` (se verifica que se llama); que dos encolados concurrentes no se pisen.

## Task 2 — `record_usage` encola cuando la base falla
**Archivos:** `backend/api/admin/usage.py`, `backend/db/migrations.py` y tests.

1. **Migración:** `axioma_usage` suma `spool_id CHAR(36) NULL` y un índice UNIQUE. Idempotente, con INPLACE/LOCK=NONE y espera acotada, igual que `_indice_de_uso_por_periodo`.
2. En el `except` de `record_usage`: antes de contar la pérdida, se intenta encolar.
   - Si encola: sube `en_cola`, NO sube `registros_perdidos` (no se perdió: está pendiente), y el log es INFO con el motivo.
   - Si NO puede encolar: ahí sí sube `registros_perdidos` y queda el WARNING de hoy. La marca `fail-soft` se reescribe para nombrar a la cola como lo que garantiza que no se pierde.
3. `registros_perdidos_stats()` pasa a devolver también `en_cola`, `perdidas_por_desborde` y `ultimo_reintento`.
4. Tests: un fallo del INSERT deja la fila en el respaldo y no cuenta pérdida; si el respaldo también falla, cuenta pérdida; el camino feliz no toca el archivo (queda vacío).

## Task 3 — El reintento
**Archivos:** `backend/uso/reintento.py` (nuevo), `backend/main.py` y tests.

1. `async def drenar(limite) -> dict`: lee pendientes, inserta cada una con `spool_id` e `INSERT IGNORE`, y quita del respaldo las que entraron (incluidas las que ya estaban, por duplicado: eso también es éxito).
   - Si la base sigue caída, no quita nada y devuelve el motivo.
   - Nunca lanza hacia afuera: un `except` fail-soft con marca.
2. `async def start_reintento_de_uso()`: mismo patrón que `start_owner_file_cleanup` — `while True`, `drenar()`, `await asyncio.sleep(JAX_USAGE_RETRY_INTERVAL_SECONDS or 60)`. Se arranca en el `lifespan` de `main.py`.
   - No arranca bajo pytest, igual que `start_facet_canary`, salvo el test que lo ejercita a propósito.
3. **Al arrancar se drena una vez antes del primer sleep**, para que un reinicio después de una caída recupere enseguida.
4. Tests: drena lo pendiente; con la base caída no pierde nada y reintenta; una fila ya insertada no se duplica (se ejercita el UNIQUE de verdad, contra la base de tests); el loop se cancela limpio en el apagado.

## Task 4 — Que se vea
**Archivos:** `backend/api/admin/usage.py` (la respuesta), `frontend/src/pages/admin/AdminCosts.jsx`, i18n es/en, tests.

1. `GET /api/admin/usage` agrega `en_cola`, `perdidas_por_desborde` y `ultimo_reintento`. Aditivo: los campos de hoy no cambian.
2. La pantalla distingue dos cosas que hoy son una sola:
   - **pendientes** (`en_cola > 0`): "hay N registros esperando reintento; el total va a completarse solo";
   - **perdidos** (`registros_perdidos > 0` o `perdidas_por_desborde > 0`): el aviso fuerte de hoy, "total incompleto".
3. Tokens del tema, i18n en los dos idiomas, y vitest para cada estado (nada, pendientes, perdidos, los dos).

## Task 5 — Carga (U29) y EXPLAIN
- El turno de chat **no puede tardar más** por esto: se mide el camino feliz contra `ae28293` y tiene que quedar igual dentro del ruido.
- Drenaje: 10.000 filas en el respaldo, cuánto tarda y cuántas por segundo, con el loop corriendo mientras entran turnos nuevos.
- `EXPLAIN` del INSERT con `spool_id` y del UNIQUE: que el reintento no haga tabla completa.
- Resultado fechado en el ledger.

## Task 6 — PR, CI, deploy y DEUDA
- **Deploy:** hay migración (columna + índice UNIQUE). Dump previo de `axioma_usage` verificado fila por fila; el script aborta si la columna o el índice no quedaron.
- Se crea `/srv/jax-data/usage-spool/` con dueño `fruiz` antes del restart.
- Smoke: el directorio existe y es escribible; `GET /api/admin/usage` sin token responde 401; el campo `en_cola` aparece en la respuesta autenticada (0).
- **DEUDA en jax:** se cierra el pendiente del 2026-09-29 y se anota uno nuevo con fecha para los **dos escritores de uso del repo jax** (`jacobs/usage_writer.py` y `las_manos/motor_registry/usage_writer.py`), que pierden filas igual y quedan fuera del alcance de esta rama.
