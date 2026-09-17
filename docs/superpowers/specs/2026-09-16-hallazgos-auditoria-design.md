# Hallazgos de la auditoría de sobre-ingeniería — diseño

> Fecha: 2026-09-16 · Autor: Mr. Hyde · Decisiones: Fernando (chat, 2026-09-16).
> Origen: auditoría de `jax-platform` en `26c9cd5` con la skill `auditando-sobre-ingenieria`
> y con `ponytail-audit`, verificadas por terceros. Evidencia cruda (fichas y veredictos) en
> el anexo A. **Regla de Fernando: se corrige todo hoy, no se deja nada para después.**

## 0. Alcance y forma

Cuatro frentes independientes, cada uno con su plan, su rama y su PR:

| Frente | Qué | Repos |
|---|---|---|
| **A** | Limpieza verificada + defectos + violaciones de regla | jax-platform (+ jax en A-22) |
| **B** | Kill switch real | jax-platform + jax |
| **C** | Ajustes de admin que mandan de verdad | jax-platform |
| **D** | Adjuntos del chat cableados | jax-platform |

Reglas comunes a los cuatro (Global Constraints de cada plan):
- TDD: test rojo contra el código viejo antes del arreglo (un control que no falla no valida).
- i18n: ningún texto visible literal; es/en en paridad. Dark/light con tokens. Nada de
  `confirm/alert/prompt`: `components/Dialogo.jsx` y `ConfirmacionSuma` para lo destructivo.
- Sin hardcoding: config en `/etc/jax/.env` o en DB.
- Fail-closed. Todo caché declara su invalidación en el mismo commit.
- Las cuatro del rendimiento: índice verificado con EXPLAIN sobre la consulta real, nada
  bloqueante en `async def`, **prueba de carga con número registrado** para todo endpoint
  nuevo o modificado en camino de usuario.
- Tests con barrera de DB de producción (`conftest.py` fuerza `jax_memory_test`).
- CI: todo test nuevo lo corre un job; se verifica rompiéndolo (rojo sobre el sha real por API).
- Mirror-sync: si se toca un símbolo espejado (`jax/scripts/check_mirror_sync.py`), el cambio
  es de los dos repos en el mismo paso.
- Deploy: backend `sudo systemctl restart jax-platform.service` con 0 pipelines en vuelo;
  frontend build → rsync a `/tmp/axioma-deploy/` en la VM dev → `sudo rsync -a --delete
  --chown=www:www --exclude .user.ini` a `/www/wwwroot/axioma-ia.io/`, con backup previo.
- Registro en la Biblioteca (`jax/DEUDA.md` y `jax/CONTEXT.md`) antes de cerrar.

---

## A. Limpieza, defectos y reglas

### A.1 Aplicar tal cual (VÁLIDO por terceros)

| id | Cambio | Dónde |
|---|---|---|
| A-01 | Quitar dependencia `@heroicons/react` (regenerar lock) | frontend/package.json:15 |
| A-02 | Quitar `aiosmtplib` de requirements y su mención en MIGRATION.md (CI de jax instala este archivo: sin efecto, nadie lo importa) | backend/requirements.txt:10, ops/migration/MIGRATION.md:118 |
| A-03 | Borrar `TokenPayload` | backend/auth/models.py:5-9 |
| A-04 | Borrar los dos `_load_jax_env()` (systemd ya carga el .env; hoy re-inyectan valores cifrados en corridas manuales) | api/chat.py:81-93, api/image.py:18-30 |
| A-05 | URL de LAS MANOS del dashboard desde `LAS_MANOS_URL` | api/admin/dashboard.py:71 |
| A-06 | Borrar `"recent_events": []` | api/admin/dashboard.py:133 |
| A-07 | Borrar `POST /api/admin/repo/save` + `SaveFileRequest` | api/admin/repository.py:88-108 |
| A-08 | MIME con `mimetypes.guess_type` | api/admin/repository.py:59-65 |
| A-09 | Quitar `send` de `createWebSocket` y el `onerror` no-op; `useWebSocket` sin return | api/websocket.js:46-48,78; store/useWebSocket.js:48 |
| A-10 | Quitar `useI18n`/`t` sin uso | pages/Admin.jsx:2,14 |
| A-11 | Borrar 25 claves i18n sin lector (lista en anexo A, ficha 16), es y en | i18n/es.js, i18n/en.js |
| A-12 | Borrar `_user_tenant_map` + su assert y docstring | jax_engine/state.py:58,77,81; tests/test_sse_isolation.py:156,178 |
| A-13 | Payload de `facet_response_completed` = `{facet}` | api/chat.py:1147-1151 |
| A-14 | Quitar eventos `image_generated` y `command_started` y sus entradas del Literal | api/image.py:98-104, api/command.py:47-53, jax_engine/schemas.py |
| A-15 | Quitar `message` del payload de `human_gate_requested` | jax_engine/state.py:217 |
| A-16 | Unificar el if/else de `_call_ollama` (conservar comentario "Bug 3") | api/chat.py:902-912 |
| A-17 | Borrar `ws_notifications` de DEFAULT_CONFIG y su clave i18n | api/admin/config_admin.py:18 |
| A-18 | CORS solo `FRONTEND_ORIGIN` (dev es mismo origen por proxy de Vite) | main.py:117-122 |
| A-19 | `_crear_token(tipo, segundos)` con wrappers públicos intactos | auth/jwt.py:18-39 |
| A-20 | Quitar guarda `accept()` inalcanzable de `WebSocketHub.connect` | jax_engine/websocket_hub.py:15-16 |
| A-21 | Quitar `Content-Type: multipart/form-data` manual (lo hace el navegador) | BottomBar.jsx:82-84 — **va en el frente D** (toca el mismo bloque) |
| A-22 | Los 10 sets de keywords de `_auto_route` quedan como copia, pero **entran a `check_mirror_sync.py`** como familia (NO importar `jax.core.router`: rompe CI) | api/chat.py:337-414 + jax/scripts/check_mirror_sync.py |
| A-23 | 5 modales hechos a mano → `Dialogo`; la revocación de AdminFacetsModels → `ConfirmacionSuma` | AdminMotors.jsx:156, AdminFacetsModels.jsx:204,232, AdminSmtp.jsx:225, PipelineModal.jsx:202 |
| A-24 | Quitar `asyncio.Lock` sin `await` entre chequeo y acción (EventBus, WebSocketHub, ResourceManager); firmas `async` intactas; `lifecycle_lock` NO se toca | jax_engine/events.py, websocket_hub.py, resource_manager.py |
| A-25 | `_MODEL_FIELDS` tupla + `", ".join` (conservar comentarios de incidentes) | api/admin/models.py:44-61 |
| A-26 | `_resolve(path)` compartido en repository (junto con el defecto A-40) | api/admin/repository.py:50-56,77-83 |
| A-27 | `PLACEHOLDERS` → ternario con fallback `''` | BottomBar.jsx:67-76 |
| A-28 | `AISLAMIENTOS` solo `READ COMMITTED` | db/transaccion.py:15,26-28 |
| A-29 | `diccionarioActivo()` exportado desde i18n, usado por el store | store/useJaxStore.js:11-13, i18n/index.jsx |
| A-30 | Inline de locales de un solo uso (conservar comentario gpt-image-1) | api/image.py:94-104 |
| A-31 | `_file_info` y `_safe_path` sin parámetros por defecto muertos | api/admin/repository.py |
| A-32 | Quitar fallbacks muertos de `buildSteps` | PipelineModal.jsx:181,188-189 |
| A-33 | `include_router` en bucle sobre tupla (conservar comentario L151) | main.py:132-156 |
| A-34 | Borrar `api/admin/facet_models.py` (desregistrado desde 2026-08-10; la tabla queda) y actualizar comentarios que lo citan | api/admin/facet_models.py, api/admin/__init__.py, main.py:151, db/migrations.py:105 |

### A.2 Con remedio corregido (PARCIAL por terceros)

| id | Diagnóstico | Remedio correcto |
|---|---|---|
| A-35 | Stat "API Keys" cuenta en .env; la verdad es la tabla `credential` | Contar credenciales activas en `credential` (consulta indexada); borrar `_count_configured_keys` y `_PROVIDERS_KEYS` |
| A-36 | Tarjeta "JAX Engine" pinguea `/health` inexistente y siempre da alive | Sondear la ruta real `/api/health` con base configurable; nunca marcar alive sin medir; test actualizado |
| A-37 | Dos `COUNT(*)` con `DATE(created_at)` | Una consulta con rango `created_at >= %s AND < %s`, `COALESCE`, misma fecha para ambos límites, test que fija el texto del WHERE (EXPLAIN no distingue en MariaDB ≥11.1) |
| A-38 | `StatCard` con alias de color | Pasar la clase completa (`tono="text-info"`), no construirla en runtime (Tailwind y `contraste.test.js`) |
| A-39 | Mapeo SMTP duplicado | Helper solo para las ramas comunes (UnicodeEncodeError→502, OSError/SMTPException→502) conservando los logs por contexto y sin interpolar `exc` en UnicodeEncodeError; `ValueError` queda en cada sitio |
| A-40 | **Defecto:** `_safe_path` usa `startswith` sin separador | `Path.is_relative_to(base)` tras `resolve()`; test con carpeta hermana `repo-x` que hoy pasa y debe fallar |
| A-41 | Lectura del audit log | `deque(maxlen=20)` con filtro de vacías **antes**, `with open`, en `asyncio.to_thread`; pasan `test_audit_ilegible.py` y `test_audit_superadmin.py` |
| A-42 | Hex de facetas en backend | Quitar `FACET_COLORS`, default `#3b82f6`/`#6b7280`; **`/api/facets` sigue leyendo `facet.color_hex`** (fuente de verdad Bloque C) |
| A-43 | Error del chat armado 3 veces con `**Error:**` literal | Helper `agregarError(facet, id, prefijoClave, detalle)` con prefijo i18n |
| A-44 | "Resolver comando" repetido en el store | Helper con parámetro `status`; el primer sitio conserva sus chequeos de sesión y existencia |
| A-45 | Defaults de etiquetas en `getEyeState` | Hacer requeridas las etiquetas (salen de i18n) y reescribir los tests que dependían de los literales |
| A-46 | `user_id=1` repetido en keys.py | Constante con nombre `USUARIO_LLAVES_LEGADO = 1` (no se quita el parámetro: los tests lo usan) |
| A-47 | `_TEST_URLS` dice que Gemini va "en query string" | Corregir el comentario (desde T6-2 va en cabecera) |

### A.3 Defectos encontrados de paso

| id | Defecto | Arreglo |
|---|---|---|
| A-48 | `/api/facets` no la llama nadie y los labels caen al nombre crudo | El store carga `display_name` (se agrega a `/api/state` desde la tabla `facet`) |
| A-49 | Stat `pipelines_completed: 0` fijo | Conteo real en `jacobs_pipelines` (status='completed', con índice verificado por EXPLAIN) |
| A-50 | Login extrae minutos con regex del texto español | Backend devuelve código estable + `retry_after_seconds`; frontend usa i18n |

### A.4 Violaciones de regla

| id | Regla | Arreglo |
|---|---|---|
| A-51 | i18n | Errores de chat/command/image/pipelines/upload devuelven **código estable** (`detail.code`) y el frontend los traduce con `codigoDe()` (api/errores.js). Incluye "Límite de N pipelines" (usa la constante/ajuste, no el literal 3) |
| A-52 | i18n | `label="RAM"` y `error: 'Error'` literales |
| A-53 | i18n | Respuestas enlatadas del chat en español (identidad del modelo, "no está disponible", modo Hyde, "Sin resultado") → código + i18n en el frontend |
| A-54 | Sin hardcoding | `seed.py`: email y tenant desde `JAX_SEED_SUPERADMIN_EMAIL` / `JAX_SEED_TENANT_NAME`; si faltan y hace falta sembrar → error explícito |
| A-55 | Sin hardcoding | `AUDIT_LOG`, `MISSIONS_DIR`, `JAX_BIN`, `REPO_BASE`, `sys.path ~/jax` → variables de entorno, mismo patrón que `JAX_REPO_PATH`; fallan cerradas si faltan |

### A.5 Se conservan (decisión con motivo, no diferidas)

| Qué | Motivo |
|---|---|
| Canal SSE `/api/events` | Infra de seguridad con cortes de sesión (b65b1f8, d9a78f9, e5e564b); el WS es el cliente real hoy |
| `PUT/DELETE/POST test` de `/api/admin/keys`, `user_api_keys` | "Red de seguridad" declarada en credentials.py:4 |
| `KeyProvider` | Diseño B1.3, deuda R2 (KMS), espejado |
| `POST /api/facets/{facet}/status` | Decisión T6-5b (2026-09-15) |
| `GET /api/pipelines*` sin cliente web | Lo usa `jax/loadtest`; aplican dueño |
| `uso/cola.contar_pendientes` | Familia espejada comparada por AST |
| `dry_run` de `/api/command` | Modo de seguridad |
| Purga de `jax_token` en localStorage | Purga de seguridad (3252d96) |
| `psutil`, `axios`, `_CacheEntry`, `exigir_un_solo_proceso`, fallback de credenciales B1.4, símbolos espejados de `cola_uso` | Verificados DAÑINOS o de bajo valor con riesgo (anexo A) |

---

## B. Kill switch real

Decisiones de Fernando: carpeta propia + ruta en .env; activar rápido, reanudar con suma.

- **Infra:** directorio `/etc/jax/interruptor/` `root:fruiz` `2770`; archivo `PAUSE`.
  `JAX_KILL_SWITCH_PATH=/etc/jax/interruptor/PAUSE` en `/etc/jax/.env`.
- **Lectores (jax):** `jacobs/policy.py`, `las_manos/server.py` (+`config.toml`
  `kill_switch_path` se elimina), `las_manos/motor_registry/routes.py` y `worker.py`,
  `las_manos/workers/ssh_worker.py`, `jax/core/main.py`. Todos leen `JAX_KILL_SWITCH_PATH`;
  **si falta la variable, arrancan con error explícito** (fail-closed: sin saber dónde está
  el freno no se ejecuta nada).
- **jax-platform:** módulo `kill_switch.py` (`estado()`, `activar(usuario)`, `reanudar(usuario)`).
  Escritura atómica. Tabla `kill_switch_audit` (id, accion `activar|reanudar`, user_id, at,
  índice por `at`). Endpoints superadmin: `GET /api/admin/kill-switch`,
  `POST /api/admin/kill-switch/activar`, `POST /api/admin/kill-switch/reanudar`.
  Evento WS `kill_switch_activated` / `kill_switch_released` a todos los canales.
- **Efecto en la Mesa:** mientras está activo, chat, image, command y creación de pipelines
  responden **423** con código `kill_switch_activo` (i18n). Lo que ya corre en LAS MANOS
  aborta por sus watchers (250 ms / 5 s); Jacobs aborta en la ola siguiente.
- **UI:** el botón muestra el estado real (GET al cargar + evento WS). Activar: `Dialogo`
  simple. Reanudar: `ConfirmacionSuma`. Se quita el `catch {}` que tragaba el error: si la
  llamada falla, el aviso lo dice.
- **Peor caso probado:** pipeline con step motor en vuelo → activar → el job queda
  `killed_by_switch` y la Mesa responde 423; reanudar → vuelve a aceptar.
- **Deploy:** crear directorio y variable con `sudo` (backup del .env), reiniciar
  `jax-las-manos`, `jax-platform` y el worker del REPL si corre, con 0 pipelines en vuelo.

## C. Ajustes que mandan

Decisión de Fernando: implementar los cinco; valor inicial = el que hoy rige en el código.

| Ajuste | Semántica | Valor migrado | Consumidor |
|---|---|---|---|
| `session_timeout_min` | Vida del refresh token (min). El access sigue en 15 min | 10080 (7 días) | auth/jwt + emisión/rotación de refresh |
| `max_pipelines` | Pipelines activos por tenant | 3 | jax_engine/resource_manager |
| `web_task_retention_days` | Retención del reaper de web-tasks | 30 | owner_cleanup |
| `lang_default` | Idioma inicial cuando el usuario no eligió | es | frontend (endpoint público de apariencia) |
| `system_name` | Título del documento, login y encabezado | valor actual | frontend (endpoint público de apariencia) |

- Módulo `ajustes.py`: lectura tipada y validada por clave (rangos del servidor, no solo del
  `<input>`); caché con TTL e **invalidación explícita en el PUT** (proceso único,
  `exigir_un_solo_proceso`). Valor ilegible en DB → error visible (503), no un default silencioso.
- Rangos de la pantalla corregidos para admitir el valor vigente (sesión hasta 10080).
- Migración idempotente que fija los valores de la tabla; dump previo de `axioma_config`
  verificado fila por fila.
- Prueba de carga de login/refresh y creación de pipeline con el ajuste leído por request.

## D. Adjuntos cableados

Decisión de Fernando: texto + imágenes + PDF.

- **Subida** (`/api/chat/upload`): tipo por **bytes** (firma mágica), no por `content_type`
  del cliente; allowlist real y fail-closed (lo que no es texto UTF-8, imagen permitida o PDF
  → 415 con código). Límites en config (`JAX_ADJUNTO_MAX_BYTES`, `JAX_ADJUNTO_MAX_CHARS`).
  PDF con **pypdf** (licencia y versión revisadas antes de instalar, versión fijada).
  Se quita la rama `pdfplumber`.
- **Chat:** `ChatRequest` declara `adjuntos` (lista tipada: `texto` con nombre y contenido
  recortado, `imagen` con mime y base64) y `extra='forbid'` para que un campo desconocido
  no se descarte en silencio.
- **Texto/PDF:** bloque de contexto con el nombre del archivo, recortado por caracteres.
- **Imagen:** solo si el modelo resuelto de la faceta (`facet_binding` → `model.input_modalities`)
  incluye `image`; si no → 422 `imagen_no_soportada` (i18n) ANTES de llamar al proveedor.
  Formato por transporte: openai-compat (`image_url` data URI), Gemini (`inline_data`),
  Ollama (`images`).
- **Memoria:** se guarda el mensaje con metadatos del adjunto (nombre, tipo, tamaño), no el
  contenido binario.
- **Frontend:** `AttachButton`/`FileAttachment` con tipos permitidos derivados del servidor,
  aviso cuando la faceta no acepta imágenes, textos i18n; se quita el header multipart (A-21).
- **Carga:** subida del tamaño máximo y chat con adjunto máximo, p95 registrado.

---

## Anexo A — evidencia

Fichas y veredictos de terceros, 2026-09-16, sobre `26c9cd5` (copiados al PR de docs):
`anexo-a/green-fichas.md`, `anexo-a/green-verif-*.md`, `anexo-a/ponytail-hallazgos.txt`,
`anexo-a/ponytail-verif-*.md`.
