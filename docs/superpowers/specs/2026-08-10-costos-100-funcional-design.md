# Pestaña de Costos — 100% funcional

**Fecha:** 2026-08-10
**Repos:** jax-platform (backend + frontend), jax (jacobs, las_manos)
**Estado:** diseño aprobado en conversación, pendiente de spec-review del usuario

## Problema

`AdminCosts.jsx` (frontend) y `GET /api/admin/usage` (backend) ya están bien
construidos — tabla, gráfico, selector de período. Pero la tabla `axioma_usage`
que los alimenta tiene `tokens_in`/`tokens_out` siempre en 0 y `cost_usd` 0.00
en el 100% de las filas. Es deuda anotada desde antes de Bloque D, nunca
reparada.

Investigación real (no supuesta) encontró 6 problemas distintos, no uno:

1. `record_usage(...)` se llama con **`0, 0` literales** en `chat.py:577` —
   no falta el dato, nunca se conectó.
2. `model_name` viene de `config["personalities"][facet]["model_default"]`
   (config.toml, fuente vieja) en vez del binding real resuelto — mismo
   patrón exacto del bug "gpt-4o para thot" ya cerrado en otro lado.
3. `image.py` nunca llama `record_usage` — costos de imágenes sin trackear.
4. Hyde nunca llega a `record_usage` (return temprano en `chat()`) y aunque
   llegara, `--output-format text` no da tokens — límite estructural ya
   conocido (mismo caso que `resolved_version`).
5. `MODEL_PRICES` en `usage.py` es una tabla de precios hardcodeada y
   duplicada (6 modelos, incluye el `gpt-4o` que ya sabemos que está mal),
   separada de `model.price_input_per_1m_usd`/`price_output_per_1m_usd`
   (Bloque D, mantenida por el sync real).
6. **Kimi no pasa por `chat.py` en absoluto** — su `transport='motor_registry'`
   no tiene rama ahí, solo es invocable vía pipelines de Jacobs (`las_manos`,
   otro servicio). Y los pipelines de Jacobs no llevan identidad real de
   usuario: `api/pipelines.py` reenvía el `body` del cliente sin inyectar
   `user.user_id`/`user.tenant_id`, y el endpoint de resume hardcodea
   literalmente `"invoked_by": "Fernando"`.

Ada (GLM-5.2) SÍ pasa por `chat.py` (`transport='http_openai_compat'`) — la
Parte A ya la cubre, sin trabajo extra.

## Decisiones ya tomadas (con el usuario, en conversación)

- **Hyde queda sin tracking, documentado** — mismo patrón que
  `resolved_version`: ausencia estructural explicada en el código, nunca un
  valor inventado. No se migra `--output-format json` en esta corrida.
- **Precios vienen de la tabla `model`** (Bloque D), no de un dict aparte —
  elimina una segunda fuente de verdad para el mismo hecho.
- **Kimi entra en el alcance** — incluye arreglar la identidad de usuario en
  pipelines de Jacobs como prerequisito (no como añadido separado).
- **La identidad de Jacobs se resuelve arreglando `invoked_by` en origen**
  (el JWT autenticado, no lo que mande el cliente) — no con un tenant fijo
  de sistema. Alcance mayor, elegido explícitamente sobre la opción más
  simple.

## Arquitectura

### Parte A — Transportes directos de la Mesa web (jekyll/hipatia/thot/ada/jax_local)

`_invoke_facet` (chat.py) pasa de devolver `str` a devolver un objeto con
`text`, `provider_id`, `model`, `tokens_in`, `tokens_out`. Cada transporte ya
recibe la respuesta completa (`data = r.json()` o equivalente) — el fix es
dejar de descartar campos que ya están ahí, no agregar llamadas nuevas:

| Transporte | Campo real (verificado con evidencia) |
|---|---|
| `http_openai_compat` | `data["usage"]["prompt_tokens"]` / `["completion_tokens"]` |
| `http_gemini` | `data["usageMetadata"]["promptTokenCount"]` / `["candidatesTokenCount"]` |
| `ollama` | `data["prompt_eval_count"]` / `["eval_count"]` — confirmado con `curl` real contra `localhost:11434` |

`record_usage()` (usage.py) cambia de firma: recibe `provider_id`/`model`
reales y tokens reales. El cálculo de costo pasa de `MODEL_PRICES[model]` a:

```sql
SELECT price_input_per_1m_usd, price_output_per_1m_usd
FROM model WHERE provider_id=%s AND model_id=%s
```

Si el modelo no está en el catálogo (nunca corrió un sync), costo queda
`NULL` con motivo visible en el registro — nunca un número inventado.
`MODEL_PRICES` y su dict se eliminan.

`image.py`: agrega una llamada a `record_usage` tras una generación
exitosa. `gpt-image-1` es costo plano por imagen (no por token) — no encaja
en las columnas del catálogo (que son por-millón-de-tokens). `record_usage`
gana un parámetro opcional `cost_usd_override` para este caso puntual,
documentado como excepción explícita, no una regla nueva del catálogo.

### Parte B — Kimi vía Jacobs/motor_registry

**B1. Identidad real en la creación de pipelines (jax-platform).**
`api/pipelines.py::create_pipeline` ya tiene `user.user_id`/`user.tenant_id`
reales (JWT autenticado) pero nunca los inyecta en el `body` reenviado a
Jacobs — hoy depende de lo que mande el cliente (que hoy es la Mesa
mandando "Fernando" fijo). El fix: inyectar `user_id`/`tenant_id` reales en
el body antes de reenviarlo, sobrescribiendo cualquier valor que mande el
cliente (nunca confiar en identidad que venga del front para algo que se
usa para atribuir costo). Mismo fix en `resume_pipeline` — reemplaza el
`"invoked_by": "Fernando"` hardcodeado.

**B2. `jacobs_pipelines` gana columnas de identidad real (jax/las_manos).**
`user_id VARCHAR(50) NULL`, `tenant_id VARCHAR(50) NULL` — separadas de
`invoked_by` (que queda como label humano, sin tocar su semántica actual).
El endpoint que crea la fila (`POST /jacobs/pipeline`, las_manos) las lee
del body y las persiste.

**B3. `jacobs/executor.py` propaga la identidad al dispatch de motor_registry.**
Al armar el `MotorDispatchRequest` para un step de Kimi, lee
`user_id`/`tenant_id` de la fila de `jacobs_pipelines` y los incluye en el
payload de `POST /motor/dispatch`.

**B4. `MotorDispatchRequest`/job de motor_registry cargan la identidad.**
`models.py`: dos campos opcionales nuevos. `worker.py`: ya captura
`usage`/`finish_reason` real (fix de hoy) — al completar un job
exitosamente, si hay `user_id`/`tenant_id` Y el motor tiene `provider_id`
mapeado (`_MOTOR_PROVIDER_MAP`), escribe un `INSERT INTO axioma_usage`
directo contra la misma DB `jax_memory` (las_manos ya se conecta ahí, mismo
patrón que `credential_resolver.py`). Costo: mismo lookup contra `model`
que la Parte A — función espejada en las_manos (mismo criterio ya usado
para `credential_resolver.py`/`model_catalog.py`: repos/venvs
independientes no justifican un paquete compartido en esta fase).

**Frontend:** sin cambios — `create_pipeline`/`resume_pipeline` del backend
ya no dependen de lo que mande el cliente para la identidad, así que
cualquier valor viejo que siga mandando la Mesa queda ignorado sin romper
nada.

## Testing

TDD en cada pieza, mismo patrón que los fixes de hoy — RED antes de
implementar:

- Parte A: extractor de usage por transporte (mock de respuesta real por
  transporte, verificado contra las shapes reales de arriba) + cálculo de
  costo contra una fila real de `model` (jax-platform, pytest, patrón ya
  usado en `test_model_catalog_sync.py`).
- `image.py`: `record_usage` se llama con `cost_usd_override`, nunca
  intenta el lookup por tokens.
- Parte B: identidad real llega de `create_pipeline`/`resume_pipeline`
  hasta el payload de `/jacobs/pipeline` (jax-platform); `jacobs_pipelines`
  persiste `user_id`/`tenant_id` (las_manos); `worker.py` escribe
  `axioma_usage` con la identidad correcta tras un job exitoso, y NO
  escribe nada si falta identidad o el motor no tiene `provider_id`
  mapeado (fail-soft, no un `INSERT` con NULLs silenciosos). las_manos no
  tiene pytest — mismo patrón de hoy (`unittest.IsolatedAsyncioTestCase`,
  stdlib, sin sumar dependencia nueva).

## Fuera de alcance (decisiones explícitas, no descuidos)

- Hyde sin tracking de costos — documentado, no simulado.
- Multi-usuario real de Anthropic — ya decidido en otra conversación de
  hoy, sin relación directa con esto.
- No se toca el schema de `model` para soportar precios no-basados-en-
  token de forma genérica — `cost_usd_override` es la excepción puntual
  para imágenes, no una nueva columna genérica.
