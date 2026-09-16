# Plan: cola durable con reintento para el registro de uso

**Pedido de Fernando (2026-09-15, chat):** "termina el pendiente del 29" — el pendiente con fecha 2026-09-29 que dejó la Task 7 de la tanda anterior.
**Base:** master `ae28293`. **Rama:** `feat/cola-durable-uso`. **Worktree:** `/home/fruiz/worktrees/jax-platform-cola-uso`.

## El problema, medido
`record_usage` (`backend/api/admin/usage.py:79`) escribe una fila en `axioma_usage` por cada turno de chat y por cada imagen. Si esa escritura falla, el `except` es fail-soft a propósito: el turno ya se cobró al proveedor y ya se le respondió al usuario, así que un 500 no recupera nada y sí le quita la respuesta. Hoy la pérdida solo se **cuenta** (`registros_perdidos`, contador en memoria desde el arranque) y se loguea. La fila se pierde para siempre, y el total de Admin → Costos queda por debajo del gasto real.

Se llama en línea, con `await`, dentro del request: `api/chat.py:1104` y `api/image.py:106`.

## La decisión de diseño
Un **respaldo propio en disco** y una **tarea de fondo que reintenta**. No una tabla: la base es justamente lo que puede estar caído.

**El respaldo es un DIRECTORIO con un archivo por fila pendiente**, no un archivo único con todas las líneas. La razón es que hay **tres procesos** que escriben `axioma_usage` y todos pierden filas igual (pedido de Fernando, 2026-09-15: "arregla esto también, no dejes nada pendiente"):
- `jax-platform`: `backend/api/admin/usage.py::record_usage` (chat e imágenes);
- `jax`: `jacobs/usage_writer.py` (los 3 transportes HTTP directos desde un pipeline);
- `jax`: `las_manos/motor_registry/usage_writer.py` (motores; hoy reintenta 3 veces y después loguea ERROR).

Con un archivo único, el que drena tiene que reescribirlo sin las filas que ya entraron, y esa reescritura pisa lo que otro proceso agregó entremedio. Con un archivo por fila no hay reescritura: se crea con `tmp` + `os.replace` (atómico) y el que drena lo borra recién después de que la fila entró. Ningún proceso toca el archivo de otro salvo para borrarlo ya insertado.

- **Dónde:** `JAX_USAGE_SPOOL_DIR` en el entorno, con default `/srv/jax-data/usage-spool/`. Los tres procesos corren como `fruiz` y ese directorio es suyo, con 1,8 T libres. El nombre de cada archivo es `<spool_id>.json`.
- **Quién drena:** sólo `jax-platform`, que es el dueño de la tabla y el único con migraciones. Los escritores de `jax` sólo depositan. Si la plataforma está caída, los archivos esperan; al arrancar drena una vez antes del primer ciclo.
- **Idempotencia:** `axioma_usage` suma una columna `spool_id CHAR(36) NULL` con índice **UNIQUE**. El reintento inserta con `INSERT IGNORE`: si el proceso se muere entre el INSERT y el borrado del archivo, el reintento siguiente no duplica el cobro.
  - Las filas del camino feliz siguen con `spool_id` NULL, y un índice UNIQUE admite varios NULL en MariaDB.
- **La hora es la del turno, no la del reintento:** el archivo guarda su `created_at` original y el INSERT lo escribe explícito. Si no, una caída de dos horas movería el costo al día siguiente.
- **Cota dura:** `JAX_USAGE_SPOOL_MAX_FILAS` (default 50.000). Pasado el tope se descarta el archivo MÁS VIEJO y se cuenta aparte (`perdidas_por_desborde`). Una caída larga no puede llenar el disco, y la pérdida sigue siendo visible.
- **Formato compartido:** el contenido de cada archivo es el contrato entre los dos repos. Se escribe una sola vez en el plan y los dos lados lo respetan: `spool_id`, `created_at` (ISO 8601 con zona), `tenant_id`, `user_id`, `facet`, `model`, `tokens_in`, `tokens_out`, `cost_usd` (número o null), `request_type`, `origen` (`platform` | `jacobs` | `motor_registry`), `status` (string o null) y `job_id` (string o null).
  - **Trece campos, no once** (DECISIÓN de Fernando, 2026-09-15, opción (b) de la consulta de las 19:3x). `status` y `job_id` sólo los llena `motor_registry`; los otros dos escritores los dejan en `null`. Sin ellos, una fila recuperada del respaldo entra a `axioma_usage` con las dos columnas en NULL y la reconciliación contra `motor_jobs.jsonl` por igualdad exacta (T3) no la puede emparejar: se recupera el cobro pero se pierde la trazabilidad.
  - Se toca AHORA, antes de que nada dependa del formato (Principio IX: no se difiere un contrato; el respaldo de producción está en 0 archivos y no hay nada desplegado). Cambiarlo después obliga a coordinar el despliegue de los dos repos con dos formatos en vuelo.
  - **Los trece campos van SIEMPRE en el archivo**, aunque dos sean `null`: `_normalizar` los completa y `_motivo_de_corrupcion` los exige. Así un archivo escrito por una copia vieja del módulo cae en `corruptos/` en vez de entrar a la base a medias — fail-closed. Obligatorios PARA EL LLAMADOR siguen siendo los nueve de siempre: `status` y `job_id` son opcionales al encolar.

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
   - `directorio_del_respaldo()` lee `JAX_USAGE_SPOOL_DIR` o devuelve el default; crea el directorio si falta.
   - `async def encolar(fila: dict) -> str | None`: escribe `<spool_id>.json` con el formato compartido de arriba, de forma atómica — archivo temporal en el MISMO directorio, `fsync` del archivo, `os.replace`, y `fsync` del directorio para que el rename sobreviva un corte de luz. Devuelve el `spool_id` o None.
   - `async def leer_pendientes(limite) -> list[dict]`: los N más viejos por fecha de archivo, cada uno con su `spool_id`.
   - `async def quitar(spool_ids) -> int`: borra esos archivos. Que un archivo ya no esté NO es error (otro ciclo pudo ganarle).
   - `def estadisticas() -> dict`: `en_cola`, `perdidas_por_desborde`, `corruptos`, `ultimo_error_de_respaldo`.
   - Todo el I/O va por `asyncio.to_thread`. Un `asyncio.Lock` del módulo serializa dentro del proceso; entre procesos la atomicidad la da el `os.replace`, no el lock.
2. **Un archivo corrupto no rompe la cola:** se saltea, se cuenta, se loguea una vez y se mueve a `corruptos/` para que el directorio no se tape con lo mismo en cada ciclo. Los demás siguen.
3. **Tope:** al encolar por encima del máximo, se descarta el archivo más viejo y sube `perdidas_por_desborde`.
4. Tests, cada uno rojo primero: encolar y leer; que el archivo temporal no quede si falla a mitad; un archivo corrupto en el medio; el tope; que se llama a `fsync` del archivo y del directorio; dos encolados concurrentes no se pisan; `quitar` de algo que ya no está no explota.
5. **El módulo es el contrato entre los dos repos:** el repo jax va a llevar una copia adaptada (Task 7), igual que ya hace con `usage_writer.py` y `db_connect_config.py`. Escribilo sin dependencias fuera de la biblioteca estándar, para que la copia sea literal.

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
- **DEUDA en jax:** se cierra el pendiente del 2026-09-29, y se registra que los tres escritores quedaron cubiertos. No queda pendiente abierto de este tema.

## Task 7 — Los dos escritores del repo jax (pedido de Fernando: "no dejes nada pendiente")
**Repo:** `jax`, worktree aparte, PR propio. **Se mergea y despliega ANTES que la plataforma** si el formato del archivo cambia; si no, después. El orden real lo fija el ledger cuando esté medido.

1. Copia adaptada de `cola.py` en `jax/core/cola_uso.py`, con symlink en `las_manos/` igual que `redaccion.py`. Mismo formato de archivo, mismo default de directorio, misma variable de entorno. Sin dependencias fuera de la biblioteca estándar.
   - Comparación por AST contra la copia de jax-platform, como se hizo con `redaccion.py`: las reglas y los cuerpos tienen que ser idénticos.
2. `jacobs/usage_writer.py`: hoy loguea el error y sigue. Pasa a encolar; si tampoco puede encolar, ahí sí ERROR.
3. `las_manos/motor_registry/usage_writer.py`: mantiene sus 3 reintentos en línea (son baratos y resuelven el caso transitorio); agotados, encola en vez de perder la fila.
4. Ninguno de los dos drena: sólo la plataforma inserta. Queda dicho en el módulo y en el comentario de cada escritor.
5. Tests en el repo jax, cada uno rojo primero: la base caída deja el archivo en el respaldo; el archivo tiene el formato compartido; si el respaldo falla, el ERROR queda; el camino feliz no deja nada.
6. Pisos de CI de jax exactos y fechados.

## Task 8 — El contrato a trece campos (DECISIÓN de Fernando, opción (b))
**Se hace ANTES que el drenaje llegue a producción. Dos mitades, en este orden:**

**8a · jax-platform** (`backend/uso/cola.py`, `backend/tests/test_cola_uso.py`, `record_usage` en `backend/api/admin/usage.py`):
`CAMPOS` pasa a trece con `status` y `job_id` al final; `CAMPOS_OBLIGATORIOS` NO los incluye
(se suman a la exclusión de `spool_id`/`created_at`); `_normalizar` los completa con `None`;
`_motivo_de_corrupcion` los exige presentes en el archivo. `record_usage` encola con los dos en `None`.
Pisos de CI re-medidos dos veces por modo.

**8b · jax** (`jax/core/cola_uso.py` + los dos escritores + tests + pisos): la copia vuelve a quedar
idéntica por AST. `motor_registry/usage_writer.py` deja de perder `status`/`job_id` en el log y los
manda en el archivo; `jacobs/usage_writer.py` los manda en `None`. **Va después de 8a**: el
`check_mirror_sync.py` compara contra la copia de la plataforma.

**Requisito de cualquier copia futura del módulo (lección del incidente 19:22-19:25):** el default del
módulo apunta al respaldo de PRODUCCIÓN por diseño. La barrera va en el `conftest.py` de la RAÍZ de
cada repo que lo copie, con `os.environ[...]` en tiempo de import (no un fixture: el que ensucia es el
test que no sabe que escribe), más un `pytest_sessionfinish` que ponga la corrida en exit 1 si apareció
un archivo nuevo en el directorio real. Y el freno se ejercita antes de darlo por bueno (Principio VII).
