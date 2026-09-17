1. `delete:` `aiosmtplib>=3.0` en requirements → nada (el SMTP usa `smtplib` de stdlib)   [backend/requirements.txt:L10]
   llamadores: `git grep -n "aiosmtplib" -- backend ':!backend/.venv'` → solo requirements.txt:10; `git log -S'import aiosmtplib'` → vacío (nunca se importó); `grep -rn aiosmtplib ~/jax` → sin resultados; tests: 0
   porqué: commit 372df1e (2026-07-08, lockout + recuperación) dice "deps: aiosmtplib". Esa feature se escribió después con `smtplib` (smtp_config.py:28). No hay decisión que lo sostenga.
   espejo: el job mirror-sync de `jax/.github/workflows/policy.yml` solo instala `pytest` y `aiomysql`, nunca este requirements → sin espejo
   equivalencia: ningún módulo lo importa. Lo único que cambia es que ya no queda instalado en un venv nuevo. ops/migration/MIGRATION.md:118 lo menciona (es documentación).
   riesgo: bajo
   tests: ninguno

2. `delete:` dependencia `@heroicons/react` → nada   [frontend/package.json:L15]
   llamadores: `git grep -n "heroicons" -- . ':!*lock*'` → solo package.json:15; `grep -rn heroicons ~/jax` → nada; tests: 0
   porqué: ninguno. `git log -S'@heroicons/react'` → solo 5e28e9e (v0.2, 2026-06-19).
   espejo: no
   equivalencia: ningún import. Hay que regenerar package-lock.json en el mismo paso.
   riesgo: bajo
   tests: ninguno

3. `delete:` rama PDF de `/api/chat/upload` (`import pdfplumber` …) → quitarla o devolver 415   [backend/api/upload.py:L52-L70]
   llamadores: BottomBar.jsx:82 (`/chat/upload`); `ls backend/.venv/.../site-packages | grep -i pdf` → nada (pdfplumber no está instalado ni en requirements); tests: `git grep -n pdf backend/tests` → 0
   porqué: ninguno. `git log -S'import pdfplumber'` → 5e28e9e (v0.2).
   espejo: no
   equivalencia: hoy todo PDF da 422 con `No se pudo leer el PDF: No module named 'pdfplumber'`. Quitar la rama hace que el PDF caiga en "text" (decode latin-1 de binario), que es peor. El reemplazo correcto es un 415/422 explícito con código i18n. AttachButton.jsx:6 ofrece `application/pdf` en el accept.
   riesgo: medio
   tests: ninguno (falta cobertura)

4. `delete:` `ALLOWED_TEXT_TYPES` y el `content_type in ALLOWED_IMAGE_TYPES or` redundante → nada / `content_type.startswith("image/")`   [backend/api/upload.py:L10-L11,L17]
   llamadores: `git grep -n ALLOWED_TEXT_TYPES` → solo su definición; `ALLOWED_IMAGE_TYPES` → solo L17; tests 0; ~/jax 0
   porqué: ninguno (v0.2, `git log -S'ALLOWED_TEXT_TYPES'`)
   espejo: no
   equivalencia: todos los tipos de ALLOWED_IMAGE_TYPES empiezan con "image/", así que el `or` ya los cubre. Resultado idéntico.
   riesgo: bajo
   tests: ninguno

5. `delete:` clase `TokenPayload` → nada   [backend/auth/models.py:L5-L9]
   llamadores: `git grep -n TokenPayload -- . ':!backend/.venv' ':!docs'` → solo la definición; tests 0; `grep -rn TokenPayload ~/jax` → 0
   porqué: ninguno (v0.2, `git log -S'TokenPayload'`)
   espejo: no
   equivalencia: no se instancia ni se importa en ningún lado
   riesgo: bajo
   tests: ninguno

6. `delete:` los dos `_load_jax_env()` (parser a mano de /etc/jax/.env al importar) → nada (systemd `EnvironmentFile=` ya carga el archivo; los tests lo cargan en conftest)   [backend/api/chat.py:L81-L93, backend/api/image.py:L18-L30]
   llamadores: solo sus propias llamadas a nivel de módulo; `/etc/systemd/system/jax-platform.service:13` y `ops/migration/systemd-units/jax-platform.service:13` → `EnvironmentFile=/etc/jax/.env`; backend/tests/conftest.py:5-23 hace su propio `_load_env` + `setdefault`
   porqué: ninguno. `git log -S'_load_jax_env'` → 5e28e9e (v0.2). Los comentarios fail-soft solo justifican el `except FileNotFoundError`.
   espejo: no está en scripts/check_mirror_sync.py
   equivalencia: como usa `setdefault`, bajo systemd no hace nada. Solo cambia para quien corra `uvicorn` a mano sin exportar el .env: hoy "funciona" y sin esto faltarían JAX_DB_* (db/connection.py ya lanza RuntimeError explícito). Un parser a mano además lee comillas distinto que systemd.
   riesgo: medio
   tests: ninguno (conftest ya carga el env)

7. `canon:` `_count_configured_keys` reimplementa el parser de .env → usar `api.admin.keys._load_env()`   [backend/api/admin/dashboard.py:L44-L66]
   llamadores: dashboard.py:113; test_dashboard_http_pooling.py (endpoint); keys.py:33 `_load_env` es el helper existente
   porqué: el comentario (triage P10, 2026-08-19) justifica acotar a FileNotFoundError; keys._load_env hace exactamente lo mismo
   espejo: no
   equivalencia: el parseo y el except son idénticos (strip, `#`, partition, FileNotFoundError). Ojo: el conteo mira .env y no la tabla `credential`, así que sigue siendo un dato viejo (ver regla).
   riesgo: bajo
   tests: ninguno

8. `delete:` auto-ping "JAX Engine" a `http://127.0.0.1:8080/health` → quitar la tarjeta, o marcarla "alive" sin red   [backend/api/admin/dashboard.py:L72,L77]
   llamadores: AdminDashboard.jsx (tarjetas de servicios); tests: test_dashboard_http_pooling.py:33-37 cuenta DOS `_check_http` por request. Evidencia: hice un GET de solo lectura a `http://127.0.0.1:8080/health` → `404 application/json`. Ese GET no debí hacerlo (no se pedía acceso a servicios); no tocó nada.
   porqué: ninguno (153f93d, 2026-07-08, stats de dashboard)
   espejo: no
   equivalencia: la ruta no existe (la del backend es /api/health). Como `status_code < 500` da "alive", hoy siempre muestra alive y además el proceso se pide a sí mismo. Quitarla cambia la lista `services` (contrato del JSON del dashboard).
   riesgo: medio
   tests: test_dashboard_http_pooling.py (pasa a esperar 1 llamada)

9. `canon:` URL literal `http://127.0.0.1:7777/health` → `f"{LAS_MANOS_URL}/health"` (jax_engine/state.py:29, que ya lee env)   [backend/api/admin/dashboard.py:L71]
   llamadores: get_dashboard; test_dashboard_http_pooling.py
   porqué: ninguno
   espejo: no
   equivalencia: con LAS_MANOS_URL sin fijar da la misma URL; con la variable fijada, el dashboard pasa a mirar el mismo LAS MANOS que el poller
   riesgo: bajo
   tests: ninguno

10. `shrink:` dos `COUNT(*)` sobre axioma_usage → uno con `COUNT(*), SUM(request_type='imagen')` y rango sargable `created_at >= %s AND created_at < %s`   [backend/api/admin/dashboard.py:L90-L100]
    llamadores: get_dashboard; test_dashboard_http_pooling.py
    porqué: ninguno (153f93d)
    espejo: no
    equivalencia: SUM devuelve NULL (no 0) sin filas, así que hace falta `COALESCE(...,0)`. `DATE(created_at)=hoy` usa `date.today()` en la zona del proceso: el rango tiene que salir de la misma fecha.
    riesgo: bajo
    tests: ninguno

11. `delete:` `"recent_events": []` constante → nada   [backend/api/admin/dashboard.py:L133]
    llamadores: `git grep -n recent_events -- frontend/src` → 0; tests 0; ~/jax 0
    porqué: ninguno (v0.2)
    espejo: no
    equivalencia: el frontend no lee la clave. Solo cambia la forma del JSON.
    riesgo: bajo
    tests: ninguno

12. `delete:` `POST /api/admin/repo/save` + `SaveFileRequest` → nada   [backend/api/admin/repository.py:L88-L108]
    llamadores: `git grep -n "repo/save"` en frontend/src → 0; backend/tests → 0; `grep -rn "repo/save" ~/jax` → 0
    porqué: ninguno (`git log -S'/repo/save'` → v0.2)
    espejo: no
    equivalencia: nadie lo llama. Quitarlo también saca un camino de escritura en disco (superadmin) que no tiene tests.
    riesgo: bajo
    tests: ninguno

13. `stdlib:` `mime = "image/png" if ext == ".png" else "image/jpeg"` → `mimetypes.guess_type(full_path)[0]`   [backend/api/admin/repository.py:L59-L65]
    llamadores: get_file ← AdminRepository.jsx (`/admin/repo/file`); tests 0
    porqué: ninguno (v0.2)
    espejo: no
    equivalencia: hoy .gif/.webp/.svg salen como image/jpeg (mal). Con mimetypes salen image/gif, image/webp, image/svg+xml. Un .svg servido como data URI image/svg+xml en `<img>` no ejecuta scripts, pero conviene confirmarlo en la verificación.
    riesgo: bajo
    tests: ninguno

14. `delete:` `send` del objeto que devuelve `createWebSocket` y `return wsRef.current` de `useWebSocket` → nada   [frontend/src/api/websocket.js:L78, frontend/src/store/useWebSocket.js:L48]
    llamadores: `git grep -n "\.send("` en src (sin tests) → solo websocket.js:22 (auth, interno); Dashboard.jsx:11 llama `useWebSocket()` sin usar el valor; websocket.test.js:16 `send() {}` es del fake de WebSocket, no de este método
    porqué: ninguno (v0.2)
    espejo: no
    equivalencia: nadie consume el valor. `wsRef` se queda porque el cleanup lo necesita.
    riesgo: bajo
    tests: ninguno

15. `delete:` `const { t } = useI18n()` y su import sin uso → nada   [frontend/src/pages/Admin.jsx:L2,L14]
    llamadores: `grep -n "\bt\." pages/Admin.jsx` → 0 usos; Admin.test.jsx envuelve en I18nProvider (sigue válido)
    porqué: ninguno
    espejo: no
    equivalencia: el componente deja de suscribirse al contexto de i18n, así que no re-renderiza al cambiar idioma (los hijos se suscriben solos)
    riesgo: bajo
    tests: ninguno

16. `delete:` 25 claves de i18n sin lector (en es.js y en.js): adminCostsPeriod, adminEventsTitle, adminKeyAddModel, adminKeyFacet, adminKeyModel, adminKeyModelAdd, adminKeyModelDelete, adminKeyModelDeleteActive, adminKeyModelDeleteConfirmButton, adminKeyModelDeleteConfirmPlaceholder, adminKeyModelDeleteConfirmTitle, adminKeyModelDeleteConfirmWrong, adminKeyModelDeleteConfirmSum, adminKeyModelName, adminKeyModelProvider, adminNav, adminProposalsCurrent, adminSettingsWsNotif, adminUserChangeRole, adminUserResetPwd, attachedFile, attachFile, attachTooLarge, attachTypes, statApiKeys → nada   [frontend/src/i18n/es.js, frontend/src/i18n/en.js (p.ej. es.js:L286,L308-L317,L489)]
    llamadores: bucle `git grep -wn <clave> -- . ':!i18n/es.js' ':!i18n/en.js'` → 0 para cada una. Descarté las familias con acceso dinámico (`adminBindingsCapability*`, `adminModelsStatus*`, `adminModelsSource*`: AdminFacetBindings.jsx:201, AdminModelCatalog.jsx:110,115). `adminKeyModelDeleteConfirmSum` solo aparece como texto de fixture en politica/dialogosDelNavegador.test.js:73 y en un comentario de dialogosDelNavegador.js:9.
    porqué: restos de AdminApiKeys/facet_models, que se retiraron el 2026-08-10 (api/admin/__init__.py:13-27)
    espejo: la paridad es/en exige borrar en los dos archivos en el mismo paso
    equivalencia: ninguna UI las muestra. Si el test de política depende del literal `adminKeyModelDeleteConfirmSum`, sigue funcionando porque es un string, no una búsqueda de clave.
    riesgo: bajo
    tests: ninguno

17. `delete:` `JAXEngineState._user_tenant_map` (se escribe y nadie lo lee) → nada   [backend/jax_engine/state.py:L58,L77,L81]
    llamadores: `git grep -n _user_tenant_map` → solo esas 3 escrituras; test_sse_isolation.py:178 `assert user_id not in engine_state._user_tenant_map`
    porqué: ninguno (v0.2)
    espejo: no
    equivalencia: nadie lo lee. La aserción del test se cubre con la de `connected_users` que ya está en el mismo test.
    riesgo: bajo
    tests: test_sse_isolation.py:178 (quitar esa línea)

18. `shrink:` payload de `facet_response_completed` (`content` con la respuesta completa + `message_preview`) → `{"facet": facet}`   [backend/api/chat.py:L1147-L1151]
    llamadores: el frontend (useJaxStore.js:309) solo mira `event_type` y pone `activeFacet: null`; tests `git grep facet_response_completed backend/tests` → 0; ~/jax 0
    porqué: ninguno (`git log -S'message_preview'` → v0.2)
    espejo: no
    equivalencia: deja de mandar la respuesta entera dos veces (HTTP y WS). El único consumidor, el canal SSE (/api/events), no tiene cliente (ver No tocar).
    riesgo: bajo
    tests: ninguno

19. `delete:` eventos `image_generated` y `command_started` (se publican y nadie los consume) → quitar la publicación y sus entradas en `EventType`   [backend/api/image.py:L98-L104, backend/api/command.py:L47-L53, backend/jax_engine/schemas.py:L6-L16]
    llamadores: `git grep -n "image_generated\|command_started" -- frontend/src` → 0; backend/tests → 0; ~/jax → 0
    porqué: ninguno (v0.2)
    espejo: no
    equivalencia: el frontend ignora tipos que no conoce. Quitarlos del Literal hace que un emisor futuro falle en validación (fail-closed).
    riesgo: bajo
    tests: ninguno

20. `delete:` `"message": "Jacobs espera aprobación para continuar"` del payload de `human_gate_requested` → nada   [backend/jax_engine/state.py:L217]
    llamadores: useJaxStore.js:305-306 usa solo `payload.pipeline_id` con `humanGateRequestedToast` (i18n); test_pipeline_user_id.py:100 mira solo el event_type
    porqué: ninguno
    espejo: no
    equivalencia: el frontend no lee `message`. De paso sale un texto en español hardcodeado.
    riesgo: bajo
    tests: ninguno

21. `shrink:` if/else de `_call_ollama` que repite la llamada → `extra = ident if facet == "jax_local" else ""; text, tin, tout = await _call_ollama(system_prompt + extra, ...)`   [backend/api/chat.py:L901-L913]
    llamadores: _invoke_facet_dispatch; tests de chat que mockean `_call_ollama`
    porqué: comentario "Bug 3" (justifica el dato de identidad, no la duplicación)
    espejo: no
    equivalencia: los argumentos son idénticos. `ident` solo se arma para jax_local.
    riesgo: bajo
    tests: ninguno

22. `canon:` 10 frozensets de keywords (`_KIMI_KW` … `_ADA_STRONG`) + `_WEB_TIEBREAK`, copiados de `jax/core/router.py` → importar `KIMI_KW…ADA_STRONG`, `_TIEBREAK` de `jax.core.router` (chat.py ya mete `~/jax` en sys.path, L105)   [backend/api/chat.py:L337-L414]
    llamadores: `_auto_route` ← chat(); backend/tests: 4 referencias a `_auto_route`
    porqué: comentario "misma lógica que router.py de consola". Sin decisión de duplicar.
    espejo: comparación por AST de los 10 sets contra jax/jax/core/router.py → los 10 idénticos (`True`, diferencia vacía). No están en scripts/check_mirror_sync.py, así que pueden divergir sin que nada avise.
    equivalencia: importar `jax.core.router` arrastra `jax.core.contrato_dispatch` (existe). Si ~/jax no está, chat.py ya falla al arrancar por `loaders` (L634-L637), así que no aparece un modo de fallo nuevo. Alternativa sin import: sumar la familia a check_mirror_sync.py (en ese caso es cambio de los dos repos).
    riesgo: medio
    tests: tests de `_auto_route` sin cambios

23. `delete:` `"ws_notifications": "true"` de DEFAULT_CONFIG + clave i18n `adminSettingsWsNotif` → nada   [backend/api/admin/config_admin.py:L18; frontend/src/i18n/es.js:L489, en.js:L486]
    llamadores: `git grep -n ws_notifications -- backend frontend/src` → solo config_admin.py; AdminSettings.jsx no lo muestra; ~/jax 0; tests 0
    porqué: ninguno (v0.2)
    espejo: no
    equivalencia: `_ensure_defaults` hace INSERT IGNORE: la fila que ya existe en producción queda y GET /admin/config la sigue listando hasta borrarla (no hace falta).
    riesgo: bajo
    tests: ninguno

24. `shrink:` alias de color en `StatCard` (`blue→text-info`, `orange→text-aviso`…) → pasar el token directo (`tono="info"`)   [frontend/src/pages/admin/AdminDashboard.jsx:L28-L44, L78-L95]
    llamadores: solo AdminDashboard.jsx (7 usos); tests `git grep StatCard -- '*.test.*'` → 0
    porqué: ninguno (153f93d; el mapa quedó al migrar a tokens)
    espejo: no
    equivalencia: el fallback `colors.slate` pasa a ser `texto` por defecto. Nombres de color que no son tokens dejarían de existir.
    riesgo: bajo
    tests: ninguno

25. `shrink:` mapeo repetido de excepciones de envío SMTP (UnicodeEncodeError → 502 smtp_password_no_ascii; ValueError → 503 smtp_config_corrupta; OSError/SMTPException → 502 smtp_envio_fallido) → un helper `smtp_config.http_de_error_de_envio(exc, contexto)`   [backend/api/admin/smtp.py:L~190-L208, backend/api/admin/users.py:L~415-L436]
    llamadores: enviar_prueba_smtp, send_reset_link; tests test_smtp_endpoints.py y test_admin_usuarios_* (fijan códigos y que nunca se loguee `exc` en UnicodeEncodeError)
    porqué: users.py:405 "Mismo juego de excepciones que /smtp/test". La duplicación es intencional para alinear comportamiento; no cita una decisión de no unificar.
    espejo: no
    equivalencia: el orden importa (UnicodeEncodeError ⊂ ValueError). users.py tiene `finally` con `enviado`, que queda afuera del helper. Los mensajes de log son distintos por contexto y hay que conservarlos. El log de UnicodeEncodeError no puede interpolar `exc`.
    riesgo: medio
    tests: ninguno (los existentes fijan el contrato)

26. `yagni:` orígenes CORS de desarrollo `http://localhost:5173`, `http://127.0.0.1:5173`, `http://localhost:8080` → solo `FRONTEND_ORIGIN`   [backend/main.py:L117-L122]
    llamadores: CORSMiddleware; frontend/vite.config.js:14-16 hace proxy de `/api` y `/ws` (el dev es mismo origen); websocket.js:5-7 en DEV va directo a :8080 por WS (CORSMiddleware no aplica a scope websocket); :8080 sirve dist desde el mismo origen
    porqué: ninguno (v0.2)
    espejo: no
    equivalencia: un cliente que llame desde :5173 SIN pasar por el proxy de vite (fetch absoluto a :8080) quedaría bloqueado por el navegador. Ningún código del repo lo hace: `baseURL: '/api'`.
    riesgo: medio
    tests: ninguno

27. `shrink:` `create_access_token`/`create_refresh_token` duplicados → `_crear_token(tipo, segundos, ...)` con los dos wrappers públicos intactos   [backend/auth/jwt.py:L18-L39]
    llamadores: api/auth.py (_emitir_tokens y otros); test_sesiones_token_version.py
    porqué: ninguno (v0.2)
    espejo: no
    equivalencia: payload idéntico si se conserva el orden de claves (JSON: el orden no afecta la validez)
    riesgo: bajo
    tests: ninguno

28. `stdlib:` `AUDIT_LOG.read_text()` + split de todo el archivo para quedarse con 20 líneas → `collections.deque(open(...), maxlen=20)`   [backend/api/audit.py:L22-L26]
    llamadores: GET /api/audit ← RightPanel/AuditLog.jsx; test (1) de get_audit
    porqué: ninguno (v0.2). El comentario Task 3 cubre el except, no la lectura.
    espejo: no
    equivalencia: hay que conservar el filtro de líneas vacías y el orden inverso. `UnicodeDecodeError` puede saltar al iterar, igual que ahora dentro del mismo try. La memoria pasa de todo el log a 20 líneas.
    riesgo: bajo
    tests: ninguno

29. `delete:` guarda `if application_state != CONNECTED: await websocket.accept()` en `WebSocketHub.connect` → nada   [backend/jax_engine/websocket_hub.py:L15-L16]
    llamadores: main.py:177 (el endpoint ya hace `websocket.accept()` en L209, antes); tests con fakes `application_state.name == "CONNECTED"` (test_websocket_isolation.py:30-35, test_sse_isolation.py:39, test_admin_usuarios_guardas.py:168)
    porqué: 31d1772 (2026-07-08, handshake de auth), cuando el accept se movió al endpoint
    espejo: no
    equivalencia: en producción la rama nunca corre. Los fakes ya la saltan. Un llamador nuevo que pase un socket sin aceptar fallaría en `send_json`.
    riesgo: bajo
    tests: ninguno (el docstring de test_websocket_isolation.py:30 queda viejo)

30. `native:` `headers: { 'Content-Type': 'multipart/form-data' }` → nada (axios 1.x con FormData en el navegador deja que el navegador ponga el boundary)   [frontend/src/components/BottomBar/BottomBar.jsx:L82-L84]
    llamadores: handleFileSelected; tests 0
    porqué: ninguno (v0.2)
    espejo: no
    equivalencia: axios ≥1 anula ese Content-Type para FormData en entorno estándar de navegador, así que la línea no hace nada. En un entorno no navegador sin boundary sería peor, pero no aplica.
    riesgo: bajo
    tests: ninguno

31. `yagni:` colores hex de facetas en el backend (`FACET_COLORS`, `FacetState.color`, `"#6b7280"`, y el override de `color` en /api/facets) → quitar el campo `color`; `DEFAULT_FACETS` pasa a ser una lista de nombres   [backend/jax_engine/state.py:L16-L27,L67,L86; backend/jax_engine/schemas.py:L38; backend/api/facets.py:L27]
    llamadores: useJaxStore.js:90-93 `_facetaDelServidor` descarta `color` explícitamente ("nada pinta con él"); /api/facets no tiene llamador en frontend; `~/jax/loadtest/api-autenticada.js:37` pide /api/facets (solo carga, no lee campos)
    porqué: ninguno para el hex. El spec tema-tokens §7.3 decidió que el frontend derive el token de la clave.
    espejo: no
    equivalencia: cambia la forma de /api/state y /api/facets (desaparece `color`). Ningún consumidor lo lee. `color_hex` de la tabla `facet` queda sin uso en este repo.
    riesgo: toca-contrato
    tests: revisar tests que comparen `model_dump()` de FacetState (no encontré ninguno con grep de "color")

## No tocar sin decisión de Fernando

- **Kill switch que no existe.** KillSwitch.jsx → `activateKillSwitch` hace `POST /api/kill-switch` (useJaxStore.js:558-564). La ruta no existe en el backend (`git grep kill.switch backend` → nada), el `catch {}` se traga el 404 y el toast dice "detenido". Además, `kill_switch_activated` nunca se emite (useJaxStore.js:300 es rama muerta). Es un freno de seguridad falso (Principio VII): implementarlo o quitarlo lo decide Fernando, no un recorte.
- **Adjuntos del chat que nunca llegan al modelo.** BottomBar.jsx:131-138 manda `image_base64`, `image_filename` y `file_context`, pero `ChatRequest` (chat.py) no los declara: Pydantic los ignora en silencio. Toda la cadena (upload.py, AttachButton, FileAttachment) es visible y no hace nada. Cablear o retirar es una decisión de producto.
- **`GET /api/facets` sin consumidor, con un bug detrás.** BottomBar.jsx:12 y PipelineModal.jsx:17 dicen que el label sale de /api/facets, pero el store solo carga /api/state (sin `display_name`), así que los labels caen al nombre crudo. La fuente de verdad es la tabla `facet` (decisión de Bloque C), y `~/jax/loadtest` usa la ruta.
- **Canal SSE `GET /api/events` sin cliente** (ni frontend ni ~/jax). Tiene historia de seguridad (b65b1f8 2026-07-18, d9a78f9 y e5e564b 2026-09-15: corte de sesiones), `lifecycle_lock`, `ChannelConnectionCounter` y `close_user_streams`. Quitar el canal es decisión de arquitectura.
- **`api/admin/facet_models.py` (124 líneas) y la semilla de `facet_models`** (db/seed.py:82-106). api/admin/__init__.py:23-27: "La tabla `facet_models` y este módulo se conservan (dato histórico, sin borrar en esta corrida)" (2026-08-10).
- **`PUT`/`DELETE`/`POST test` de `/api/admin/keys`, `_write_env_key` y `user_api_keys`.** Ningún llamador en frontend; el test tiene llamadores en tests. `_write_env_key` reescribe /etc/jax/.env entero (pierde comentarios). credentials.py:4: "api/admin/keys.py (user_api_keys) queda INTACTO — es la red de seguridad".
- **`_TEST_URLS` duplicado de `PROVIDERS[test_url]`** (credentials.py:27-35 vs keys.py). El comentario dice "no se unifica en esta fase". Además dice que Gemini va "en query string", cosa que desde T6-2 ya no es cierta.
- **`KeyProvider` ABC con una sola implementación** (crypto_secrets.py:9-27). Diseño B1.3 (docs/fase1-credenciales-diseno.md), deuda R2/LUKS declarada; `crypto_secrets` es familia espejada en check_mirror_sync.py.
- **`repository._safe_path` usa `target.startswith(base)`** (L14-L19): `~/jax/repo2` pasa el chequeo. Es una guarda de rutas. El arreglo (`os.path.commonpath`) cambia una validación: no es un recorte.
- **`POST /api/facets/{facet}/status` sin llamador.** T6-5b (2026-09-15) decidió conservarlo como herramienta de admin, solo superadmin.
- **`GET /api/pipelines` y `GET /api/pipelines/{id}` sin llamador en frontend.** T6-5a (2026-09-15) reescribió la lista con índice y regla de dueño; los dos aplican propiedad.
- **Ajustes de admin que nadie lee:** `session_timeout_min` (el JWT es fijo de 15 min), `max_pipelines` (UI 1..5 contra `MAX_PIPELINES_PER_TENANT = 3` fijo), `web_task_retention_days` (el reaper usa 30 días fijos en owner_cleanup.py:24), `lang_default` y `system_name`. Son límites y retención visibles en AdminSettings.jsx que no controlan nada. Implementar o retirar: Principio IX.
- **Stat `pipelines_completed: 0` constante** (dashboard.py:125), mostrado en AdminDashboard.jsx:79: el tablero afirma un dato que no mide.
- **`uso/cola.contar_pendientes` solo con llamadores de test.** `uso/cola.py` es familia espejada (canónico en jax-platform) y se compara por AST en el CI de jax.
- **Modo `dry_run` de `/api/command`** (command.py:105): el frontend siempre manda `execute`, pero es un modo de seguridad.

## regla:

- `regla:` i18n. BottomBar.jsx:154,190,212 muestran `**Error:** ${err.response.data.detail}`: prefijo literal y texto del backend en español crudo (chat/command/image/pipelines devuelven textos, no códigos: p.ej. pipelines.py:120, upload.py:34, chat.py "Error en {facet}: …").
- `regla:` i18n y contrato frágil. Login.jsx:47 extrae minutos con `/(\d+)\s*minuto/` del texto español `"Cuenta bloqueada. Intenta de nuevo en N minuto(s)."` (api/auth.py:118). Remedio: código estable + campo numérico o Retry-After, como ya hace el 429.
- `regla:` i18n. AdminDashboard.jsx:92 `label="RAM"` literal (existe `statRam` pero con otra forma). AdminFacetsModels.jsx:50 `error: 'Error'` literal.
- `regla:` i18n. Respuestas del chat en español hardcodeadas: chat.py `_MODEL_IDENTITY_HOSTING` y `_model_identity_reply` (L~790-L805), `"⚠️ {facet} no está disponible: …"` (3 variantes), `"Hyde opera en modo tarea autónoma…"`. command.py:120 `"[Sin resultado — JAX no produjo output]"`, que el frontend muestra tal cual.
- `regla:` sin hardcoding. db/seed.py:49,66,78: email `fernando@rich-hn.com` y tenant `'Inversiones Diamante Negro'` literales en el código.
- `regla:` sin hardcoding (rutas y config). audit.py:11 `AUDIT_LOG`, command.py:17-18 `MISSIONS_DIR`/`JAX_BIN`, repository.py:10 `REPO_BASE`, chat.py:105 `sys.path ~/jax`: todas relativas a `$HOME` sin variable de entorno, mientras chat.py:78 y :634 ya usan `JAX_CONFIG_PATH`/`JAX_REPO_PATH`.
- `regla:` sin hardcoding. pipelines.py:120 `"Límite de 3 pipelines concurrentes alcanzado"` repite `MAX_PIPELINES_PER_TENANT` (resource_manager.py:4) como literal.
- `regla:` sin hardcoding (colores). schemas.py:38 `"#3b82f6"`, state.py:16-25 `FACET_COLORS` y state.py:86 `"#6b7280"`: hex en el backend (ver ficha 31).
- `regla:` las cuatro del rendimiento (indexing). dashboard.py:91,97 filtran con `DATE(created_at) = %s` (no sargable) en el camino del dashboard. `/api/admin/usage` ya usa un índice cubriente con rango.
- `regla:` verdad de dato. dashboard.py:44-66 cuenta keys "configuradas" leyendo /etc/jax/.env, pero la fuente real es la tabla `credential` (credential_resolver); el tablero puede afirmar N/5 falsamente.

neto estimado: -330 líneas, -2 dependencias (aiosmtplib, @heroicons/react), sin verificar por terceros