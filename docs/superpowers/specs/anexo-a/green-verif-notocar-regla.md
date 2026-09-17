Revisé el código en HEAD `26c9cd5`, solo lectura. Las cuatro afirmaciones graves se sostienen. Hay 22 bullets verdaderos, 3 parciales y ninguno falso. Solo discuto la derivación a Fernando en un caso, el de `_safe_path` (#9).

**No tocar sin decisión de Fernando**

1. **TRUE.** Kill switch:
   - `useJaxStore.js:558-564` hace `POST /kill-switch` con `catch {}` vacío. Después siempre sale el toast `killSwitchStoppedToast` ("todos los procesos detenidos").
   - Ningún router en `backend/main.py:132-156` define esa ruta. `/home/fruiz/jax` tampoco tiene ruta HTTP de kill-switch.
   - El proxy de `vite.config` apunta a :8080, así que jax-platform no reenvía a jax.
   - `kill_switch_activated` solo aparece en `schemas.py:10` y nadie lo emite, así que la rama de `useJaxStore.js:300` está muerta.
   - Detalle: el POST probablemente choca con el mount de archivos estáticos en `main.py:329` y devuelve 405, no 404. Lo tapa igual.
   - Contexto que falta: jax sí tiene un kill switch real por archivo (`jacobs/policy.py:27` `KILL_SWITCH_PATH.exists()`), con lo que cablearlo es posible.
   - Derivarlo: correcto.
2. **TRUE.** Adjuntos: `BottomBar/BottomBar.jsx:131-138` manda `image_base64`, `image_filename` y `file_context`. `ChatRequest` (`chat.py:472-483`) solo declara message, facet, project_id y origin, sin `extra="forbid"`, así que Pydantic descarta el resto. La subida (`upload.py`, `/api/chat/upload`) funciona, pero su resultado se pierde. Derivarlo: correcto, es decisión de producto.
3. **TRUE.** `BottomBar.jsx:12-13` y `PipelineModal.jsx:17` citan `/api/facets`. Nadie en frontend la llama. El store solo hace `api.get('/state')` (`useJaxStore.js:568`) y `state.py` no tiene `display_name`, así que el label cae a `name` o al id. `jax/loadtest/api-autenticada.js:37` sí la usa. Derivarlo: correcto.
4. **TRUE.** `events.py:62` no tiene cliente ni en `frontend/src` ni en jax. Los commits `b65b1f8` (2026-07-18), `d9a78f9` y `e5e564b` (2026-09-15) existen. `lifecycle_lock`, `ChannelConnectionCounter` (`lifecycle.py:15`) y `close_user_streams` están presentes. Derivarlo: correcto.
5. **TRUE.** `facet_models.py` tiene 124 líneas. El comentario citado está en `admin/__init__.py`, el router está desregistrado (`main.py:151`) y la semilla está en `seed.py:~82`. Derivarlo: correcto.
6. **PARTIAL.**
   - Correcto: PUT, DELETE y POST test (`keys.py:161,203,228`) no tienen llamador en frontend. POST test tiene llamadores en `test_keys_http_pooling.py:40,64`. `_write_env_key` (`keys.py:47-55`) reescribe `/etc/jax/.env` sin comentarios. La cita de `credentials.py:4` es exacta.
   - Impreciso: `user_api_keys` sí se usa desde el frontend, vía `GET /admin/keys` (`AdminFacetsModels.jsx:30,65`).
   - Derivarlo: correcto, es la "red de seguridad" declarada.
7. **TRUE.** `credentials.py:29-35` duplica `PROVIDERS[test_url]` de `keys.py:23-27`, con "no se unifica en esta fase". El comentario de Gemini "en query string" está desactualizado desde `8552f4b` (T6-2). Derivarlo: correcto.
8. **TRUE.** `crypto_secrets.py:9-27` tiene `KeyProvider` con una sola implementación, `EnvKeyProvider`, y cita B1.3 y R2. El archivo está espejado en `jax/scripts/check_mirror_sync.py:193-200`. Derivarlo: correcto.
9. **TRUE.** Es un bypass real, detrás de superadmin:
   - `repository.py:14-19` usa `target.startswith(base)` con base `~/jax/repo`.
   - `GET` o `DELETE /repo/file?path=documents/../../repo2/x` pasa, porque `ALLOWED_FOLDERS` solo valida la primera parte.
   - Eso deja leer o borrar en carpetas hermanas cuyo nombre empiece por "repo". Hoy no existe ninguna en `~/jax`.
   - Derivarlo: discutible. Hace bien en no tratarlo como recorte, pero es un bug de seguridad que solo endurece la validación, sin decisión de producto que tomar. Por la regla de "no deferred findings" habría que arreglarlo directamente.
10. **TRUE.** `facets.py:30-35` tiene el comentario de T6-5b, `require_superadmin`, y no tiene llamador. Derivarlo: correcto.
11. **TRUE.** `pipelines.py:101` y `:169` no tienen llamador en frontend. El frontend solo usa POST, `/results`, `/resume` y `/cancel`. La lista usa `SQL_PIPELINES_DEL_USUARIO` y el detalle, `_require_pipeline_owner`. `jax/loadtest` sí usa `/api/pipelines`. Derivarlo: correcto.
12. **TRUE.** Las cinco claves solo se escriben y leen en `config_admin.py:13-19` y `AdminSettings.jsx`. Nada del backend ni de jax las consume (`axioma_config` solo lo leen `apariencia.py` y `smtp_config.py`). Valores fijos que ignoran esos ajustes:
    - `jwt.py:10`: `ACCESS_EXPIRE_SECONDS = 15*60`.
    - `resource_manager.py:4`: `MAX_PIPELINES_PER_TENANT = 3`, contra la UI de 1 a 5 (`AdminSettings.jsx:97`).
    - `owner_cleanup.py:24`: 30 días fijos.

    Derivarlo: correcto.
13. **TRUE.** `dashboard.py:125` tiene `"pipelines_completed": 0`, que se muestra en `AdminDashboard.jsx:79`. Derivarlo: correcto, aunque sería candidato fácil a implementar.
14. **TRUE.** `uso/cola.py:574` solo tiene llamadores en tests. `check_mirror_sync.py:315-322,375` incluye `contar_pendientes` en la comparación. Detalle: el canónico declarado ahí es `jax/core/cola_uso.py`, no jax-platform como dice el bullet. Derivarlo: correcto.
15. **TRUE.** `command.py:~105` tiene la rama `dry_run`. `BottomBar.jsx:178` siempre manda `mode: 'execute'`. El `dry_run` de `PipelineModal` es de otro endpoint. Derivarlo: correcto.

**regla:**

- **R1 TRUE.** `BottomBar.jsx:154,190,212` tienen `**Error:** ${detail}` (detail sale de `err.response?.data?.detail`). Los textos del backend confirmados: `pipelines.py:120`, `upload.py:34`, `chat.py:1080`.
- **R2 TRUE.** `Login.jsx:47` tiene `/(\d+)\s*minuto/`. El texto sale de `auth.py:118`. La rama del 429 ya usa `Retry-After` (`Login.jsx:52`, `rate_limit.py:37`).
- **R3 TRUE.** `AdminDashboard.jsx:92` tiene `label="RAM"` y `statRam` existe como función. `AdminFacetsModels.jsx:50` tiene `error: 'Error'`.
- **R4 TRUE.** Las líneas están algo corridas: `_MODEL_IDENTITY_HOSTING` y `_model_identity_reply` en `chat.py:799-810`, los tres "no está disponible" en 857, 896 y 936, "Hyde opera…" en 1049, y el texto de `command.py` en la 121, no la 120.
- **R5 TRUE.** `seed.py:49` es un mensaje de log con el email, `seed.py:66` tiene el tenant y `seed.py:78` el email.
- **R6 TRUE.** `audit.py:11`, `command.py:17-18`, `repository.py:10` y `chat.py:105` están atados a `$HOME` sin variable de entorno. En cambio `chat.py:78` y `:634` usan `JAX_CONFIG_PATH` y `JAX_REPO_PATH`.
- **R7 TRUE.** `pipelines.py:120` tiene el literal "3" y `resource_manager.py:4` la constante.
- **R8 TRUE.** Hex en `schemas.py:38`, `state.py:16-25` y `state.py:86`.
- **R9 PARTIAL.** `dashboard.py:91,97` usan `DATE(created_at) = %s`, pero no es un problema real de rendimiento. Según la nota `feedback-explain-igual-no-valida-el-cambio` (medido el 2026-09-15), MariaDB 12.3.3 convierte `DATE(col)` en rango. Además `idx_axioma_usage_periodo` empieza por `created_at`, así que en la práctica probablemente usa el índice. Vale como tema de estilo o portabilidad; no verifiqué la igualdad con `EXPLAIN` porque no se podía tocar la DB.
- **R10 TRUE.** `dashboard.py:44-66` (`_count_configured_keys`) lee `/etc/jax/.env`. La fuente real es la tabla `credential`, que usan `credential_resolver.py`, `chat.py` e `image.py`.

**Conteo**
- **No tocar:** 14 TRUE, 1 PARTIAL (#6), 0 FALSE. Derivación correcta en 14 de 15; la de #9 debería ser un arreglo directo.
- **regla:** 9 TRUE, 1 PARTIAL (R9), 0 FALSE.