# E3 · respaldo de PDF escaneado en el chat

**Fecha:** 2026-10-06
**Estado:** ronda 3 implementada en la rama `feat/e3-respaldo-chat`. La auditoría de escalón 3 sobre `b209f7de` dio **APROBADO CON CAMBIOS** (2 MAJOR, 5 MINOR; ningún BLOCK) y la ronda 3 los cerró; la auditoría de escalón 3 sobre `365adba` dio **APROBADO** (veredicto comunicado por el coordinador; no es una aprobación de quien implementó) con tres MINOR que cierran los commits posteriores a `365adba` (cupo en el camino de error y de cancelación, límites conocidos de los topes y esta evidencia): esos commits **no están auditados**. **CI de `365adba`: policy run [37502134709](https://github.com/fjruizhn/jax-platform/actions/runs/37502134709)**, todos los jobs requeridos en verde: frontend 1410 (conteo **exacto**: el job falla si no son 1410), backend sin DB y backend con DB con su piso respetado (el runner solo comprueba `passed >= piso`, **no** un conteo exacto: 2364 y 3853 son mínimos), con el paso `Medir E3...` en verde y el paso `Comprobar que E3 rechaza el mutante sleep(0.5)` pasando porque el mutante cae en rojo, como corresponde. La medición real de LAS MANOS queda pendiente para la ventana indicada abajo. El reporte de carga más antiguo se conserva como historia, pero su prueba fue declarada inválida para medir E3 por la auditoría: medía solo latencia de chat con un PDF pequeño ya en proceso, sin cronometrar subidas ni un estado terminal. No acredita el comportamiento de subida, despacho y sondeo hasta finalización.

## Corrección del registro (2026-10-06)

La medición histórica de 721.1 ms del POST de chat no corresponde al criterio E3. La prueba vigente (ronda 3, `test_carga_e3_20_usuarios_chatean_mientras_se_procesan_sus_pdf`) mide **por separado**, con latencia **por petición** y no con el tiempo de pared del lote, el p95 de cada subida (`POST /api/chat/upload`, 20 PDF escaneados al máximo de páginas y bytes) y el p95 de cada turno de chat (5 por usuario), mientras el hilo principal corre el despacho (`despachador.ciclo`) y el sondeo reales y consulta el estado de cada documento; las llegadas son escalonadas y el OCR simulado tarda 3 s, de modo que hay chats mientras se procesa (la prueba lo exige: >= 90 % de los turnos con documentos abiertos y >= 3 vueltas de despacho durante los chats). Un segundo paso del workflow inyecta `time.sleep(0.5)` bloqueante en `encolar_pdf_desde_chat` y solo se considera una demostración válida si el fallo es la aserción del tope de p95 (`E3 subida p95` o `E3 chat p95`).

El primer resultado normal de CI del SHA `18c4364` (policy run 37484684852) fue `p95=max=1891.9 ms`. Su corrida mutante no fue válida: el `sleep(3)` al inicio de `encolar_pdf_desde_chat` llenó el pool OCR de un worker y devolvió `503 adjuntos_reintentar` antes de medir latencia. Por ello, el paso mutante fija solo para esa corrida 8 workers (máximo permitido) y 60 s de timeout (máximo permitido), para separar bloqueo del event loop de saturación OCR.

**Histórico (SHA `65b9449`, prueba ANTERIOR; no es evidencia del SHA final):** policy run [37491432941](https://github.com/fjruizhn/jax-platform/actions/runs/37491432941) midió `p95=1308.5 ms`, `max=1309.7 ms`, que era el tiempo de pared del lote completo (p95 y máximo difieren en menos de 1 ms), con tope 25000 ms; el auditor de `b209f7de` demostró que ese tope dejaba pasar un `time.sleep(0.5)` en la ruta (p95 1132 -> 11152 ms). El run **37492767675** corrió sobre `88e4a52` y el **37494514448** sobre `b209f7de`; ninguno es del SHA final de esta ronda.

## Cierre de la auditoría sobre `b209f7de` (ronda 3, 2026-10-06)

**Cómo se midió (local, hall9000, no CI):** MariaDB `mariadb:12.3.3` efímera propia (nombre único, `--rm`; nunca el `3308` ni `jax_memory`), con las pruebas dentro del espacio de red de su contenedor (así LAS MANOS real, `7777`, y Ollama, `11434`, son inalcanzables por construcción), venv de pruebas con los extractores y el clon de jax en el pin de CI `0604754`. El OCR real no se mide aquí.

**Línea base de la prueba de carga (12 corridas sin mutante, 20 usuarios x 5 turnos, 20 PDF de 20 páginas y 10 MiB):**

| Medida | Rango | Peor corrida | Tope | Margen declarado |
|---|---:|---:|---:|---|
| p95 de subida | 32-60 ms | 59,9 ms | ~~1500 ms~~ (reemplazado el 2026-10-06 por el latido del loop, ver «Vigilancia del bloqueo del event loop») | x25: válido solo con 32 CPU; el runner de CI midió 1215-1669 ms sin mutante |
| p95 de turno de chat | 1021-1623 ms | 1623 ms | 5000 ms | x3: la base ya es de cola (100 turnos en ~2 s sobre un loop) |

Mutante `time.sleep(0.5)` en `encolar_pdf_desde_chat`, con el entorno del paso de CI (8 workers, 60 s): **subida p95 9828 / 9918 / 10130 ms** (tope absoluto de entonces: 1500; hoy es un techo de 8000 y la vigilancia es el latido del loop) y chat p95 3908 / 3713 / 2132 / 3577 ms en cuatro corridas (con subida 10110 ms en la última); la prueba quedaba en rojo siempre por `E3 subida p95` (hoy cae por `E3 loop`, ver la sección siguiente). El tope de chat (5000 ms) no ve este mutante, a propósito: el bloqueo no está en la ruta del chat y esa cifra es cola. **Ese tope NO promete detectar un bloqueo pequeño en `chat()`: ver «Límites conocidos de los topes».** **La prueba ANTERIOR (`b209f7de`) con el mismo mutante a 0,5 s pasó** (p95 11062 ms contra tope 25000): ese era el defecto. Sin los 8 workers el mutante da `503 adjuntos_reintentar` antes de medir, por eso el paso de CI los fija y el grep exige el mensaje del tope.

## Vigilancia del bloqueo del event loop: latido, no tope de subida (2026-10-06)

**Defecto original:** `tope_subida_ms=1500` era absoluto y se calibró en hall9000 (32 CPU, base 32-60 ms). La subida p95 bajo carga depende de los núcleos: en el runner de CI, **master** dio 1215 ms y los runs de **#210** 1616 y 1669 ms (verificado en los logs `E3_LOAD`) sin cambio en la ruta de subida. El control fallaba por la máquina, no por el código.

**Intento descartado (45cb3e7, rechazado por la auditoría de #211):** un tope relativo `6 x costo_aislado x 20` con la subida aislada medida por la misma ruta vigilada. Era circular: una regresión real en `encolar_pdf_desde_chat` infla el costo aislado y el p95 por igual, el tope relativo subía hasta el techo de 8000 ms y un `sleep` de 0,05-0,3 s REAL pasaba 4 de 4. Además la «subida aislada» no estaba aislada: cada subida disparaba `despachar_ahora` real. Se quitó entero.

**Diseño vigente: un LATIDO en el loop de la app.** Lo que la prueba vigila es que la ruta de subida no BLOQUEE el event loop. Una tarea lanzada con `client.portal.start_task_soon` corre en el MISMO loop donde corre la app (el del portal de `TestClient`), duerme 10 ms en bucle y registra cuánto se atrasa cada despertar. Un bloqueo síncrono de X ms en el loop atrasa el latido >= X ms con cualquier número de núcleos; el trabajo legítimo en `to_thread` no lo atrasa (control negativo de la auditoría: `to_thread` de 0,2 s pasa).

**Aserciones (en este orden; todas con el prefijo `E3 loop:` que exige el grep del mutante en `policy.yml`):**

| Aserción | Tope | Sin regresión (36 corridas, 1/2/4/32 CPU) | Qué ve que las otras no |
|---|---|---|---|
| p99 del atraso | 40 ms (`JAX_E3_LATIDO_P99_MAX_MS`) | 2,4-19,6 ms | tolera un tirón aislado del runner; ve el bloqueo repartido en muchas subidas |
| atraso máximo | 100 ms (`JAX_E3_LATIDO_MAX_MS`) | 4,0-33,7 ms | un bloqueo único de cualquier tamaño >= 100 ms (el p99 no ve <= 4 eventos) |
| tiempo total bloqueado (suma de los atrasos > 20 ms) | 200 ms (`JAX_E3_LATIDO_BLOQUEADO_MAX_MS`) | 0-65,6 ms | la regresión chica y repartida (`sleep(0.03)` en todas las subidas) |

- **Por qué estos valores:** cada tope es aprox. la media geométrica entre lo peor sin regresión y lo menor con regresión que debe atrapar: p99 40 (19,6 vs 75 con `sleep(0.05)`); máximo 100 (33,7 vs 301, la menor con 4 subidas de 0,3 s; un bloqueo único de 1 s da 995-1000); total 200 (65,6 vs 520, la menor con `sleep(0.03)`).
- **Mínimo detectable declarado:** un `sleep` de **0,03 s** en todas las subidas (6/6), un bloqueo único de **>= 100 ms**, o bloqueos que sumen **> 200 ms**. NO se ve: `sleep(0.02)` en todas las subidas (total 20-192 ms, máx 20-45 ms: 0/6) ni un bloqueo único menor a 100 ms.
- **Que el latido nunca cuelgue la prueba:** el latido se detiene en un `finally` que envuelve todo lo que va entre su arranque y las aserciones, y tiene su propio tope de vida (180 s). Antes (cff1173), si un hilo de usuario fallaba (503/429, `TimeoutError`, assert), el latido seguía vivo y el cierre del portal de `TestClient` esperaba para siempre su tarea: la prueba se colgaba sin log (el job no tiene `timeout-minutes` por defecto: 6 h). **Verificado:** con un 500 inyectado en la 5.ª llamada a `encolar_pdf_desde_chat`, cff1173 se cuelga (rc=124 a los 60 s, sin una línea de log) y el código nuevo falla en 8 s con `AssertionError: {"detail":{"code":"inyectado_e3"}}` (`1 failed`).
- **`timeout-minutes: 30` en el job `backend-tests-con-db`** (`policy.yml`): sin él, un cuelgue ocupaba el job hasta el tope por defecto de 6 h. Medido en hall9000 con 4 CPU y MariaDB efímera: suite completa 200 s (3931 pruebas pasan; en esa corrida fallaron 4: 3 pruebas de `test_base_de_test` que lanzan un subproceso sin `CI=true` y lo rechaza el guardia de producción del arnés local, y `test_no_fail_open_except`, que atrapó un `except Exception` sin marca en esta misma prueba y se corrigió antes del commit), E3 ~6 s y su mutante ~15 s; 30 min deja ~9x de margen para instalación, arranque de MariaDB y un runner más lento.
- **El latido muerto tiene otro prefijo:** `E3 latido muerto: casi no corrió (N despertares)` (mínimo 50). Antes compartía el prefijo `E3 loop:` con el bloqueo real, y el paso del mutante lo habría dado por bueno. El grep exige `^E +AssertionError: E3 loop: `.
- **Red:** techo absoluto de subida p95 `TECHO_SUBIDA_MS = 8000` y tope de chat 5000 ms. `JAX_E3_SUBIDA_P95_MAX_MS` ya no existe en la prueba ni en `policy.yml`.
- **Qué imprime `E3_LOAD`:** `latido_n`, `latido_max_ms`, `latido_p99_ms`, `latido_sobre_tope`, `latido_bloqueado_ms`, sus topes, `subida_p95_ms`, `subida_techo_ms` y los de chat: el runner deja su línea base registrada.

**Mediciones (hall9000, MariaDB 12.3.3 efímera `--network none`, `taskset`; código final):**

Sin regresión (4 corridas por configuración; todas pasan):

| CPU | p95 subida (ms) | p99 latido (ms) | máx latido (ms) | total bloqueado (ms) |
|---|---|---|---|---|
| 1 | 973-1778 | 4,7 / 7,8 / 3,8 / 7,2 | 13,2-20,1 | 0 / 20,1 / 0 / 0 |
| 2 | 1089-1410 | 4,1 / 3,4 / 2,4 / 3,2 | 8,1-19,7 | 0 |
| 4 | 513-1009 | 5,0 / 4,2 / 5,1 / 4,0 | 18,4-23,4 | 23,4 / 20,3 / 41,0 / 0 |
| 32 (sin restringir) | 37,5-47,5 | 3,2 / 5,5 / 3,1 / 2,6 | 12,6-22,2 | 0 / 42,3 / 21,9 / 0 |

Las otras 20 de esas 36 corridas (5 por configuración) dieron p99 2,4-19,6 ms, máximo 4,0-28,3 ms y total 0-65,6 ms; una tanda anterior llegó a un máximo de 33,7 ms.

Regresiones REALES en el código (inyectadas dentro de `encolar_pdf_desde_chat`, en el árbol, no el mutante del test), todas en 1 y 32 CPU:

| Regresión | Corridas | p99 (ms) | máx (ms) | total bloqueado (ms) | Falla |
|---|---|---|---|---|---|
| un solo bloqueo de 1 s (1 subida de 20) | 4 | 2,6-13,0 | 995-1000 | 1136-1177 | 4/4 |
| un solo bloqueo de 3 s | 4 | 4,1-13,2 | 2994-3001 | 3141-3179 | 4/4 |
| un solo bloqueo de 5 s | 4 | 2,8-15,6 | 4992-4995 | 5100-5164 | 4/4 |
| cada 5.ª subida 0,3 s (4 subidas) | 4 | 30,6-299,7 | 301-603 | 1214-1313 | 4/4 |
| cada 10.ª subida 1 s (2 subidas) | 4 | 5,8-14,8 | 994-1008 | 2067-2075 | 4/4 |
| `sleep(0.05)` en todas | 6 | 74,8-124,3 | 118-298 | 990-1136 | 6/6 |
| `sleep(0.03)` en todas | 6 | 30,1-55,1 | 31-64 | 520-654 | 6/6 |
| `sleep(0.02)` en todas | 6 | 17,9-20,5 | 20-45 | 20-192 | 0/6 (no detectable) |
| mutante del test (CI, `sleep(0.5)`) | 8 | 501-1011 | 2500-4022 | 10083-10214 | 8/8, por `E3 loop` |

Con solo el p99 (cff1173), los bloqueos únicos de 1, 3 y 5 s y la regresión de cada 10.ª subida pasaban siempre (p99 de 3 a 16 ms) y la de cada 5.ª subida de 0,3 s pasaba a veces (p99 30,6 en 1 de 4). El grep de `policy.yml` encuentra la línea real del mutante.

**Falsos fallos con contención fuerte (declarado, medido).** Con la prueba en 1 CPU y 2 o 3 procesos que consumen CPU en esa misma CPU (emulación extrema de un vecino ruidoso, mucho peor que el runner normal), el latido da falsos fallos:

| Competidores en la CPU | Corridas | p99 (ms) | máx (ms) | total (ms) | Falsos fallos |
|---|---|---|---|---|---|
| 2 | 4 | 17,8-63,2 | 34-89 | 84-521 | 3/4 |
| 3 | 4 | 39,8-61,8 | 68-135 | 444-703 | 4/4 |

De los 7 fallos, 3 son solo por el tiempo total bloqueado (p99 <= 40 ms): **el tope total agrava los falsos fallos** (solo con el p99 y el máximo habrían sido 4 de 8; con el total, 7 de 8). Con 1 CPU y UN competidor (la emulación de runner lento anterior) no hubo falsos fallos en la prueba de entonces. Si el runner real se parece al caso de contención, hay que re-medir los topes, no subirlos a ciegas.

El número de pruebas no cambia: `--collect-only` de `tests/test_adjuntos_chat_endpoint.py` da 32 antes y después.


**Límites conocidos de los topes (declarados tras la auditoría de `365adba`):**

- **Ningún tope ve un bloqueo en `chat()` de hasta 0,1 s por turno.** Según la auditoría, un `time.sleep` bloqueante en la ruta del chat de 0,02 s, 0,03 s o 0,05 s por turno **pasa** la prueba. Con 0,1 s por turno **tampoco cae por ningún tope**: el auditor lo reprodujo 2 de 2 veces con subida p95 1309 / 1452 ms (tope 1500) y chat p95 4434 / 4029 ms (tope 5000). Lo que cae es la **validez del escenario** (aserción «>= 90 % de los turnos con documentos abiertos»: 71/100), y eso **depende de la máquina**: la prueba lo detecta, sin garantía, solo porque el bloqueo alarga el escenario hasta que los documentos terminan antes que los chats. El tope de chat solo atrapa regresiones grandes de esa ruta: la base ya es cola (1021-1623 ms con el mismo loop compartido por 100 turnos) y x3 sobre ella deja mucho espacio.

  | Bloqueo por turno en `chat()` | ¿La prueba cae? | Qué la hace caer |
  |---|---|---|
  | 0,02 s | no | — |
  | 0,03 s | no | — |
  | 0,05 s | no | — |
  | 0,1 s | a veces (2 de 2 en la máquina del auditor) | ningún tope; la aserción del escenario (71/100 < 90 %), sin garantía |

- **La vigilancia de la subida es el latido del loop (ver «Vigilancia del bloqueo del event loop»): solo ve bloqueos del event loop.** Una espera no bloqueante por subida, por ejemplo `await asyncio.sleep(1.0)`, **pasa** (no atrasa el latido; con p95 de subida de 1048 ms según la auditoría). Un retraso que no bloquea el loop (una consulta lenta, un acceso lento a disco en `to_thread`, un `await` de red) solo cae si lleva la subida p95 sobre el techo de 8000 ms. El mínimo detectable de un bloqueo síncrono en la ruta de subida es 0,05 s por subida.
- **El margen x25 del tope absoluto de subida (1500 ms) no valía fuera de hall9000, y se reemplazó.** Corregido con datos del runner de CI (verificados en los logs `E3_LOAD`): master dio subida p95 **1215 ms** y los runs de #210 **1616 y 1669 ms**, sin que el código de subida cambiara; la base de decenas de ms es de una máquina de 32 CPU. Un tope absoluto de latencia mide la máquina, no el código. El run 37502134709 que había pasado era una sola observación. El primer run de CI con el latido deja en su `E3_LOAD` la línea base del runner (`latido_p99_ms`); si ahí el p99 sin regresión se acerca a 40 ms, el tope se re-mide, no se sube a ciegas.
- Ninguno de los dos topes mide OCR real: el borde HTTP de LAS MANOS está simulado (`_OCR_SIMULADO_S`).

| Hallazgo | Cierre | Evidencia local |
|---|---|---|
| MAJOR 1 · la carga no medía «chatear mientras se procesa» ni detectaba 0,5 s | Prueba reescrita (arriba): despacho y sondeo intercalados con los chats, p95 por petición de subida y de chat por separado, topes = base medida x margen declarado; el paso mutante de `policy.yml` pasa a `sleep(0.5)` y a ambos topes | Base y mutante en la tabla de arriba |
| MAJOR 2 · la prueba del reintento fallaba `GET /proyectos`, no el sondeo | Las pruebas de sondeo usan reloj falso y mocks **por URL** (`/proyectos/7/documentos/42` falla; la lista del selector responde aparte): esperas exactas 10 y 20 ms, tope de espera en 40 ms, tope total, y consulta que nunca vuelve | Mutantes F3 (catch -> agotar), F4 (backoff constante), F4b (sin tope de espera) y F5 (sin `topeTimer`): las pruebas nuevas fallan en cada uno; la prueba vieja los dejaba pasar a los tres (26/26 verdes) |
| MINOR 3 · `npm test` fallaba en local | `VITE_CHAT_PDF_POLL_*` pasan a `frontend/vitest.config.js` (`test.env`: 10, 40, 400 ms) y se quitan del paso de CI: iguales en local y en CI | `npx vitest run` sin variables: 1410/1410; la prueba vieja sin esa configuración fallaba (5003 ms de espera) |
| MINOR 4 · F7, F10, F9 sin prueba y falta `estados.desconocido` | Pruebas de `FileAttachment` (sin «listo» en `en_cola`/`pendiente`/`procesando`, estado traducido, «desconocido» traducido) y de `BottomBar` (`project_id` en el formData); `estados.desconocido` en es y en | F7, F10 y la clave faltante: rojo contra el mutante; F9: rojo sin el `append` |
| MINOR 5 · M9, M11, M7 sin prueba | Un VIEWER no lee un documento oculto (404 aun para el dueño); duplicado desde el chat devuelve el documento canónico con su estado actual y el oculto da 409 sin revelar el id; `DOC_MAX_BYTES_ARCHIVO` menor que el tope del chat da 413 con el tope de proyectos | M9 (sin `AND oculto_at IS NULL`), M11 (sin camino de reuso, estado fijo, sin id del duplicado) y M7 (sin chequeo de bytes en `encolar`, sin el `min()` en `upload.py`): rojo en cada uno |
| MINOR 6 · `encolar_pdf_desde_chat` no tomaba `cupo_de_subidas` | Toma el cupo de subidas simultáneas antes de tocar el disco y lo suelta en `finally` (429 `subidas_simultaneas`, ya traducido); pruebas con N=1 por usuario y global, con dos subidas realmente simultáneas | Contra `b209f7de` (sin cupo) las dos pruebas fallan (200 en vez de 429); sin el `soltar` fallan por cupo no liberado |
| MINOR 7 · este informe | Se quitó «Auditoría escalón 3: APROBADO»; «Contexto fijado» corregido; la evidencia de CI es el run 37502134709 (SHA `365adba`); lo posterior queda marcado como pendiente | Este documento |

**Efecto de producción a notar:** con los valores sembrados (`subidas_por_usuario=2`, `subidas_globales=4`), una ráfaga de PDF escaneados por el chat ahora puede recibir 429 `subidas_simultaneas` (el área de proyectos ya se comportaba así). La prueba de carga sube el cupo al máximo con `ajustes_en_db` porque mide la latencia del loop, no el rechazo del cupo.

**Pisos:** medidos en local; en el runner solo se verifica el mínimo (passed ≥ piso), salvo el frontend, que es exacto. Para `365adba` (run 37502134709): frontend **1410/0/1410** (113 archivos; antes 1397); backend sin DB **al menos 2364** (el runner solo verifica el piso) (localmente 2364 / 1491 skipped; antes 2364 / 1485: las +6 nuevas piden `client`); backend con DB, piso **3853** respetado (localmente 3854 passed / 1 skipped; antes, mismo método sobre `b209f7de`: 3848 / 1, y CI midió 3847 / 2: la diferencia de 1 es una prueba que el runner omite por entorno). Después de `365adba` se agrega una prueba con DB (cancelación del PDF del chat): localmente **3855 passed / 1 skipped**, así que el piso con DB pasa a **3854**; **ese +1 no está medido en el runner**. Frontend y backend sin DB no cambian.

**Pisos tras el rebase sobre `origin/master` `303006f` (que ya trae #199 y #200; sus pisos: 3836 con DB, 2361 sin DB, 1389 frontend).** Los pisos se suman a los de master con el delta de E3, no se eligen: con DB 3836 + 24 = **3860**, sin DB 2361 + 6 = **2367**, frontend 1389 + 21 = **1410**. Medidos sobre la mezcla, en local (MariaDB 12.3.3 efímera propia en su netns, puerto distinto de 3306 y 3308, venv con los extractores, jax en el pin de CI): frontend **1410/0/1410** (113 archivos), sin DB **2367 passed / 1495 skipped**, con DB **3861 passed / 1 skipped** (piso 3861 − 1 = 3860, el 1 que el runner omite por entorno). **Ninguno está medido en el runner**; el runner solo prueba `passed >= piso` en los dos de backend (mínimos, no conteos exactos) y exactamente 1410 en frontend. Lo re-mide quien integre (regla 5). El run 37502134709 de arriba corresponde a `365adba` sobre la base anterior, no a la mezcla.

**Pendiente de esta ronda:** (1) auditoría de escalón 3 de los commits posteriores a `365adba`, en otra invocación; (2) policy run del SHA final (el de `365adba` ya pasó, ver arriba) y re-medición del piso con DB (+1); (3) integración por Fernando, porque el PR toca `.github/workflows/policy.yml`.

## Cierre del informe de auditoría de la ronda 2 (histórico, SHA `65b9449`)

| Hallazgo | Cierre | Evidencia en el SHA `65b9449` |
|---|---|---|
| Major · E3 no medía upload→terminal ni detectaba `sleep(3)` | 20 subidas válidas al máximo de páginas/bytes, chat, despacho durable y sondeo terminal; p95 con tope de CI y mutante ejecutado en configuración OCR acotada válida | Policy run 37491432941: `p95=1308.5 ms`, `max=1309.7 ms`; mutante falló por p95 a `61448.8 ms` sobre `25000 ms`; wrapper del mutante pasó al verificar la causa exacta |
| Minor 1 · códigos internos OCR visibles | Causa y estado se resuelven por catálogos i18n; código desconocido usa causa genérica | Tests `FileAttachment`; frontend `1397/1397` |
| Minor 2 · sondeo sin reintentos ni límite | Reintento ante error transitorio, backoff creciente y tope total configurable con estado honesto al agotar | Tests `BottomBar`; frontend `1397/1397` |
| Minor 3 · selector bloqueado tras estado terminal | Se desbloquea en estados finales y muestra instrucción localizada | Tests `BottomBar` y `FileAttachment`; frontend `1397/1397` |
| Minor 4 · el freno no se revalidaba en la entrada nueva | Se revisa antes de autorización, carpeta, escritura e INSERT. Si para un lote después de aceptar archivos, el 423 incluye el resultado parcial; al fallar pre-INSERT elimina solo el archivo no registrado | Job DB `3847 passed`; dos escenarios parametrizados verifican una sola fila/archivo y el resultado parcial |
| Minor 5 · faltaban denegaciones de autorización del nuevo punto de entrada | Pruebas de otro tenant, sin membresía, VIEWER, archivado e inexistente; espían autorización de escritura/estado activo y confirman que no se abre carpeta ni se transmite archivo | Cinco casos parametrizados pasaron en job DB; `3847 passed, 2 skipped, 0 failed` |

Los floors medidos quedan en frontend 1397, backend no-DB 2364 y backend con DB 3847; no se redujeron. La medición E3 representa el runner efímero de CI con el borde HTTP de LAS MANOS simulado; no representa OCR real ni una medición de producción.

(Ronda 2, histórico; en la ronda 3 sí se ejecutaron localmente con una MariaDB efímera propia en su netns.) La integración E3 y los cinco casos DB de autorización todavía no se ejecutaban localmente: en Hall9000 pytest cerró antes de conectarse porque `3308` está protegido como puerto de producción, Docker no permite usar el daemon y no hay `mariadbd` local. El job `backend-tests-con-db` usa MariaDB efímera y quedó como ejecutor de esos casos. No se habilitó `JAX_TEST_DB_PERMITIR_INSTANCIA_DE_PRODUCCION` y no se consultó ni modificó `jax_memory`.

Verificaciones de esta rama en Hall9000: frontend `1397 passed` (113 archivos); unidad backend de estado/freno `3 passed`; el PDF de muestra se parseó a 20 páginas y 10 MiB y produjo `PdfSinTexto`. La suite backend completa sin DB terminó `2362 passed, 1482 skipped, 2 failed`; los dos fallos son `test_processing_status_is_resolved_only_by_paired_jax_singleton` y `test_processing_status_without_authoritative_jsonl_is_unavailable`, porque el checkout local de JAX no contiene `procesamiento_routes`. CI clona el pin JAX exacto de su workflow. Esos fallos no son evidencia de resultado para el job CI.

El OCR real de LAS MANOS no puede medirse en el job CI, que simula solo el borde HTTP remoto. **Pendiente para 2026-10-07; responsable: Fernando asigna operador y ventana de carga en el host LAS MANOS antes del GO de producción.** Registrar allí configuración efectiva, concurrencia, peticiones/s, p95 y máximo upload→terminal, latencia de chat y uso de CPU/memoria. Hasta que se mida, no se declara medido ni listo para GO de producción.

## Intento y resultado (histórico, no acredita E3)

La carga solicitada se ejecutó con 20 usuarios enviando turnos de chat mientras un PDF escaneado estaba admitido en la cola durable y el sondeo del dispatcher recibía `running`. La base MariaDB, las membresías, la admisión, la transición durable, el dispatcher y `/api/chat` fueron reales dentro del job de CI; LAS MANOS respondió con un cliente HTTP simulado que mantuvo el estado en proceso. El proveedor de chat simulado esperó 30 ms por petición. No se usó producción ni se simula que OCR terminó.

El intento local detectó `JAX_DB_PORT=3308`, puerto de la instancia de producción, y rechazó crear una base de pruebas (`base_de_test.py::exigir_conexion_permitida`). No se habilitó `JAX_TEST_DB_PERMITIR_INSTANCIA_DE_PRODUCCION`. Docker está instalado, pero su daemon devuelve `permission denied` en `/var/run/docker.sock`; tampoco hay `mariadbd`/`mysqld` instalado para iniciar una instancia desechable. La prueba se trasladó al job `backend-tests-con-db`, que levanta MariaDB efímera `mariadb:12.3.3`.

| Medida | Resultado |
|---|---:|
| Usuarios concurrentes de chat | 20 |
| Solicitudes de chat completadas | 20/20 HTTP 200 |
| PDF escaneado | Admitido y marcado `pendiente`; dispatcher activo y consulta retenida en `running` |
| p95 del POST `/api/chat` | 721.1 ms |
| Máximo del POST `/api/chat` | 769.4 ms |
| Base de datos de producción `jax_memory` usada | No |

Medición: GitHub Actions run [37454479696](https://github.com/fjruizhn/jax-platform/actions/runs/37454479696), SHA `30989bf5184bbb1573170c49d317ee16f2cd1281`; el paso focal `Medir E3 con 20 usuarios y escaneo en proceso` imprime el resultado. El job completo reportó 3838 pasadas y 2 omitidas. Esta es una prueba concurrente de extremo a extremo del backend bajo un proveedor de chat con demora fija, no una carga sostenida ni una medición de OCR real.

## Lecturas verificadas

- La ruta de subida encola el original en `project_documents` y avisa al despachador sin esperar al trabajo de LAS MANOS.
- La cola y su sondeo HTTP del procesador corren en el despachador existente; este cambio no llama OCR dentro del turno de chat.
- Las pruebas sin DB no demuestran latencia ni integración real del despachador.
- Verificación local completa sin DB: backend `2363 passed, 1479 skipped`; frontend `1393 passed, 0 failed`. Los tests que abren MariaDB se omitieron por `JAX_CI_NO_DB=1`.
- Delta de piso medido contra `origin/master d283d90`: backend `+5` pasadas (dos casos de upload sin DB, dos lecturas unitarias de estado y una comprobación del sufijo PDF); frontend `1389→1393` (`+4`: estado OCR, turno sin contenido OCR, traducción de autorización y selector bloqueado para conservar el proyecto). El test `FileAttachment` preexistente ya estaba versionado en `origin/master`.

## Pendientes vigentes

1. Medir OCR real de LAS MANOS según la ventana y responsable indicados arriba. La evidencia de CI no reemplaza esta medición.

## Registro de trabajo y traspaso

**Objetivo:** registrar el PDF escaneado del chat en el proyecto autenticado y entregar respuesta sin esperar el OCR; mostrar después el estado verdadero del procesador.

**Hecho (2026-10-06):** `PdfSinTexto` ahora lleva el total de páginas. `/api/chat/upload` exige `project_id` para el escaneado, vuelve a validar membresía/papel de escritura y proyecto activo, limita bytes/páginas con la configuración existente, y reutiliza el guardado durable, deduplicación y dispatcher de `project_documents`. El nuevo GET de estado comprueba visibilidad del proyecto y oculta documentos ocultos/ajenos con 404. BottomBar sondea el estado y la tarjeta usa i18n es/en y `aria-live`. No se envía el PDF al modelo ni se presenta como leído: E2b no está implementado.

**Decisión técnica:** se reutiliza el dispatcher existente, que ya llama el API vigente de LAS MANOS; no se crea una tarea efímera en FastAPI ni se espera OCR dentro de la petición. La suba de chat no crea un adjunto de texto que el modelo pudiera confundir con extracción real. La decisión procede del alcance E3 del encargo, no de una nueva decisión de negocio.

**Contexto fijado:** al iniciar una subida se captura el proyecto autenticado seleccionado. El selector queda deshabilitado durante la subida y mientras el documento siga en `en_cola`, `pendiente` o `procesando`; **se desbloquea en los estados finales** (`listo`, `parcial`, `error`, `sin_extractor`, `cancelado`) y cuando el sondeo agota su tiempo (`estado_no_disponible`), aunque la tarjeta siga en el compositor, y entonces se muestra la instrucción de que ya se puede cambiar de proyecto. Así no se puede enviar bajo el proyecto B un turno mientras el PDF recién admitido, todavía en proceso, pertenece al A que estaba seleccionado al iniciar la carga. (Código: `BottomBar.jsx`, prop `bloqueado` de `SelectorDeProyecto`; pruebas en `BottomBar.test.jsx`.)

**Pruebas:** en GitHub Actions `37454479696`, backend sin DB: 2363 pasadas / 1477 omitidas; backend con DB: 3838 pasadas / 2 omitidas; frontend y guardias pasaron. Localmente el backend sin DB había dado 2363 / 1479 y el frontend 1393 / 0. La integración de cola y el arnés de 20 usuarios sí corrieron en MariaDB desechable en CI. Los pisos sin DB suben backend +5 y frontend +4 respecto a `origin/master d283d90`; el piso DB quedó medido en 3838.

**Pendiente:** reauditoría adversarial aprobada del SHA final y cierre de CI tras actualizar el piso a 3838. La primera reauditoría rechazó `ff619e7` por persistir la posibilidad de cambiar de proyecto después de iniciar una subida; ahora el selector se bloquea durante la subida y mientras el PDF permanece en el compositor. Sus hallazgos anteriores de bloqueo de turno, traducción de autorización, tipo PDF y procedencia del piso se corrigieron. No se integró ni desplegó.

**Incidente de CI (2026-10-06):** la primera corrida aislada de backend con DB en PR #208 reportó `1 failed, 3836 passed, 2 skipped`. El único fallo fue `test_pdf_escaneado_desde_chat_se_encola_y_expone_su_estado`: el test construía `AuthUser.tenant_id` con el alias textual de `Entorno`, mientras la ruta que guarda en proyectos requiere el ID numérico real de tenant. El harness `JAX_CI_NO_DB=1` omitía ese camino. Se corrigió el fixture usando `tests.identidades._tenant_db_id`; la suite DB posterior pasó.

**Corrección de test y medición (2026-10-06):** la preparación de la carga usa ahora `POST /api/chat/upload` autenticado. El job de CI `37454479696` confirmó la integración y midió 20/20 turnos con p95 721.1 ms y máximo 769.4 ms. La primera corrida que alcanzó la carga dejó una fila abierta que contaminó una prueba posterior; se añadió un finalizador que borra esa fila solo en la MariaDB desechable. La corrida final pasó `3838 passed, 2 skipped`; piso DB actualizado de 3830 a 3838 conforme a la medición del runner.

**Alternativas descartadas:** no conectar el test harness al puerto 3308, porque es producción; no autorizar el opt-in que el harness ofrece; no usar un mock para atribuirle un p95 real. Las tres decisiones evitan mezclar un ensayo con operación real o convertir una simulación en evidencia falsa.

## Traspaso registrado

**Fecha y autor:** 2026-10-06, Codex.

**Objetivo:** cerrar E3 para PDFs escaneados subidos desde el chat, usando la cola durable del proyecto y mostrando estados reales sin esperar OCR en la petición.

**Hecho:** commits locales `8161f02` (admisión a cola), `8b1883d` (evidencia y handoff inicial), `ff619e7` (turno de texto no espera OCR) y `2984c5f` (selector ligado al contexto del PDF); el SHA de PR #208 se encuentra después de estos commits. La auditoría adversarial rechazó `8b1883d`, `ff619e7` y `a998c98`; cerró hallazgos funcionales, pero mantuvo bloqueada la evidencia DB/carga. El selector ahora se bloquea durante toda subida y mientras el PDF escaneado esté en el compositor. Suite frontend 1393 y backend no-DB 2363/1479 skips pasan.

**Pendiente:** corrida CI del commit que actualiza el piso/documentación y auditoría aprobada sobre SHA final. La carga de 20 usuarios y la integración DB ya pasaron. No se tocó `jax_memory` ni producción. No se editó `PENDIENTES.md`.

**Decisiones y alternativas:** usar `project_documents` y su dispatcher proviene de la arquitectura del proyecto verificada por el arquitecto Tier 3. No llamar LAS MANOS directamente ni usar una tarea efímera proviene del contrato vigente de cola. No presentar un p95 simulado y no conectarse al puerto 3308 provienen del encargo y de la regla de carpintero. El selector se bloquea mientras se sube porque la admisión duradera ocurre antes del POST del turno, y habilitar el cambio permitiría dos contextos distintos.

**Cierre:** la medición de carga y la auditoría adversarial del SHA `c2b7e1d` concluyeron. Se mantiene PR #208 en borrador hasta que CI termine verde sobre el head final. No integrar ni desplegar desde esta sesión. El checkout principal y producción permanecen intactos.

**Cierre de ronda 2 (2026-10-06):** los hallazgos del informe de auditoría `1 MAJOR, 5 MINOR` se atendieron en la rama `feat/e3-respaldo-chat` (SHA de código `65b9449`). Ese cierre **no cuenta como aprobación**: el `APROBADO` que se registró entonces lo dio un auditor invocado por quien implementó, y la auditoría independiente de `b209f7de` resultó **APROBADO CON CAMBIOS** con 2 MAJOR y 5 MINOR, atendidos en la ronda 3 (arriba). El OCR de LAS MANOS real continúa como pendiente fechado y asignado a Fernando; no se ejecutó producción ni se integró a `main`.
