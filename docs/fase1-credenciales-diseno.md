# Fase 1 — Credenciales de proveedor a la DB como fuente de verdad

**Fecha:** 2026-08-09 · **Rama:** `infra/mariadb-12.3-migration` · **Estado:** B1 — diseño en papel, sin ejecutar.
**Ejecuta sobre:** MariaDB 12.3 (Bloque A cerrado y verificado en PARADA 3).
**Base factual:** `docs/auditoria-api-keys-2026-08-09.md` (R3) + B0 de esta corrida.
**Fuera de alcance** (anotado si aparece, no implementado): `facet_models`, consolidación de facetas, literales de Jacobs en `executor.py`, las 10 listas paralelas, los 3 despachadores, catálogo de modelos, botón de sincronización, `axioma_usage`/costos.

---

## 0. B0 — hallazgos que fijan este diseño

**B0.1 — El backup de R2 anula el cifrado: SÍ, confirmado.**
`backup-hall9000.sh:24` define un único `STAGING="/srv/backup-adata/staging"`. El dump de `jax_memory` (incluye `user_api_keys.encrypted_value`) va a `$STAGING/mariadb-local/jax_memory.sql` (línea 85). `/etc/jax/.env` (incluye `FERNET_KEY` en claro) se copia a `$STAGING/jax-config/env` (línea 182). `restic backup ... "$STAGING"` (líneas 205-209) respalda el directorio padre completo — ambos archivos, mismo snapshot, a Local **y** R2. No se ejecutó `restic` para esta verificación, solo se leyó el script.

**B0.2 — Consumidores de credenciales de proveedor: 4 procesos reales** (no 5 — `embedding_worker.py` se verificó y no consume ninguna credencial de proveedor, solo Ollama local vía `MemoryDB`, queda excluido):

| Proceso | Archivo:línea | Vía |
|---|---|---|
| `jax-platform.service` | `backend/api/chat.py:490,499,508,520,530`, `backend/api/image.py:41` | `os.getenv()` directo — funciona porque `main.py:13-14` descifra todo `os.environ` al arrancar el proceso |
| `jax-las-manos.service` (Jacobs embebido) | `jacobs/executor.py:262,331,362,396`, `jacobs/plan.py:196,219` | `os.environ[...]` directo — funciona porque `las_manos/server.py:39-40` descifra al arrancar |
| REPL de jax | `jax/muscles/base.py:166-176` (vía `main.py`) | `decrypt_secret()` explícito por key |
| `jax-memory-worker.service` | `jax/muscles/base.py:166-176` (vía `worker.py` → `build_extractor()`) | `decrypt_secret()` explícito por key |

**B0.3 — Puntos de acceso a `FERNET_KEY`: exactamente 3 archivos, ninguno más** (grep exhaustivo, 0 coincidencias fuera de estos):
`jax-platform/backend/crypto_secrets.py`, `jax/jax/core/crypto_secrets.py`, `jax/las_manos/crypto_secrets.py` — las 3 copias espejo creadas en el fix de hoy temprano. Esta es la superficie completa de B1.3.

---

## 1. B1.1 — DDL de las tres tablas nuevas

Reemplazan el hardcodeo de `PROVIDERS` en `api/admin/keys.py:17-23` (hoy: agregar un proveedor = editar Python) y extienden el modelo de `user_api_keys` (hoy: 1 credencial por proveedor, sin estados, sin auditoría).

```sql
CREATE TABLE provider (
  id            VARCHAR(50)  NOT NULL PRIMARY KEY,   -- mismo namespace que provider_id ya usado en user_api_keys/facet_models: 'openai','deepseek','gemini','moonshot','zhipu','ollama','anthropic'
  display_name  VARCHAR(100) NOT NULL,
  base_url      VARCHAR(255) NULL,                    -- NULL para 'anthropic' (Hyde = subprocess, no HTTP)
  auth_type     ENUM('api_key','none','subprocess') NOT NULL,
  is_local      TINYINT(1)   NOT NULL DEFAULT 0,       -- 1 para 'ollama' (jax_local): sin credencial, sin egress
  status        ENUM('active','deprecated') NOT NULL DEFAULT 'active',
  created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TABLE credential (
  id                 INT AUTO_INCREMENT PRIMARY KEY,
  provider_id        VARCHAR(50)  NOT NULL,
  env_key            VARCHAR(100) NOT NULL,            -- se conserva durante B1.4 (doble lectura); ver nota de deprecación en B1.4
  encrypted_value    TEXT         NOT NULL,             -- Fernet — ver B1.3, protege el dump a R2, no protege contra compromiso del host
  state              ENUM('active','rotating','revoked') NOT NULL DEFAULT 'active',
  created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  activated_at       DATETIME     NULL,
  revoked_at         DATETIME     NULL,
  last_verified_at   DATETIME     NULL,
  last_health_status ENUM('ok','failed','unknown') NOT NULL DEFAULT 'unknown',
  last_health_detail VARCHAR(255) NULL,                 -- mensaje corto del último test, nunca el valor de la key
  created_by         INT          NULL,                 -- FK jax_users.user_id, quién la creó/rotó
  FOREIGN KEY (provider_id) REFERENCES provider(id),
  FOREIGN KEY (created_by) REFERENCES jax_users(user_id),
  INDEX idx_provider_state (provider_id, state)          -- NO UNIQUE: hasta 2 filas 'active' por provider_id, requisito de solapamiento
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;

CREATE TABLE credential_audit (
  id                  INT AUTO_INCREMENT PRIMARY KEY,
  credential_id       INT          NULL,                -- NULL solo si la accion fallo antes de crear la fila (ej. intento de alta invalido)
  provider_id         VARCHAR(50)  NOT NULL,             -- denormalizado: permite auditar por proveedor aunque credential_id sea dificil de trazar
  action              ENUM('create','rotate','revoke','view','test') NOT NULL,
  performed_by        INT          NOT NULL,             -- FK jax_users.user_id — NUNCA nulo, toda accion la hace un superadmin autenticado
  performed_from_ip   VARCHAR(45)  NOT NULL,              -- IPv4/IPv6
  performed_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  detail              VARCHAR(255) NULL,                 -- motivo opcional, nunca el valor de la credencial
  FOREIGN KEY (credential_id) REFERENCES credential(id),
  FOREIGN KEY (performed_by) REFERENCES jax_users(user_id),
  INDEX idx_provider_time (provider_id, performed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_uca1400_ai_ci;
```

**Por qué `credential_audit` no puede ser el plugin de auditoría de MariaDB** (nota de diseño explícita pedida): el plugin de auditoría del servidor solo ve `JAX_DB_USER` (el usuario de conexión de la aplicación, único y compartido por todo Axioma) — no puede distinguir qué superadmin humano ejecutó la acción HTTP que disparó el `UPDATE`. `performed_by`/`performed_from_ip` son datos de capa de aplicación (vienen del JWT y del `Request` de FastAPI), no reconstruibles desde el log del servidor MariaDB en ninguna versión.

**Migración de datos**: las 5 filas actuales de `user_api_keys` se migran a `credential` con `state='active'`, `provider_id`/`env_key`/`encrypted_value` copiados tal cual (mismo cifrado Fernet, no se re-cifra). `user_api_keys` **no se borra ni se altera** — ver B1.5.

## 2. B1.2 — Resolver compartido

```python
# credential_resolver.py — espejado en los 3 codebases (mismo patrón que crypto_secrets.py:
# repos/venvs independientes, no justifica un paquete compartido en esta fase).

CREDENTIAL_CACHE_TTL_SECONDS = int(os.getenv("CREDENTIAL_CACHE_TTL_SECONDS", "30"))
CREDENTIAL_STALE_MAX_SECONDS = int(os.getenv("CREDENTIAL_STALE_MAX_SECONDS", "300"))

class CredentialUnavailableError(Exception):
    """FAIL-CLOSED: no hay credencial valida. El llamador declara estado
    degradado explicito — nunca cae a un default silencioso."""

async def resolve_credential(provider_id: str) -> str:
    cached = _cache.get(provider_id)
    if cached and not cached.is_expired(CREDENTIAL_CACHE_TTL_SECONDS):
        return cached.value                                    # (a) fresco, por request logicamente — el cache de 30s es la unica excepcion permitida
    try:
        row = await _query_active_credential_from_db(provider_id)  # SELECT ... WHERE provider_id=%s AND state='active' ORDER BY activated_at DESC LIMIT 1
        value = decrypt_secret(row.encrypted_value)
        _cache.set(provider_id, value, fetched_at=now())
        return value
    except (DBConnectionError, NoActiveCredentialError) as e:
        if cached and not cached.is_stale_beyond(CREDENTIAL_STALE_MAX_SECONDS):  # (c) techo del stale
            log.warning(f"credential_resolver provider={provider_id} db_unreachable=1 serving_stale_age={cached.age()}s")
            return cached.value
        log.error(f"credential_resolver provider={provider_id} FAIL_CLOSED reason={type(e).__name__}")
        raise CredentialUnavailableError(provider_id) from e   # (d)(e) fail-closed explicito, nunca silencioso
```

- **(a) Resolución por request, sin caché de vida de proceso** — cumplido: el caché tiene TTL de 30s, no vive lo que dure el proceso. Un proceso de vida larga (`jax-las-manos`) nunca sirve una credencial de más de 30s de antigüedad salvo degradación explícita.
- **(b) TTL = SLA de revocación, explícito**: **30 segundos** en el caso normal (DB sana). Es el compromiso: una key revocada sigue siendo usable hasta 30s después de la revocación en el peor caso. Configurable vía `CREDENTIAL_CACHE_TTL_SECONDS`. Se eligió 30s como balance entre no saturar la DB en pipelines de Jacobs con múltiples steps rápidos (varios `_invoke_*` en la misma ola) y mantener el SLA de revocación en un orden de magnitud útil para un incidente de seguridad real.
- **(c) Caché acotado con techo**: si la DB cae, se sirve el último valor bueno marcado `stale`, pero **nunca más de 300 segundos (5 min)** desde que se obtuvo. Pasado ese techo, degrada explícito (d). Sin este techo, una DB caída por horas reintroduciría exactamente R3 (key vieja viviendo indefinidamente), solo que ahora en la app en vez de en el `.env`.
- **(d) FAIL-CLOSED**: sin credencial válida (ni fresca ni stale dentro del techo) → `CredentialUnavailableError`. El llamador (`_invoke_jekyll`, `_invoke_hipatia`, etc.) debe capturarla y reportar la faceta como degradada — nunca debe interpretarse como "sin key, seguir con string vacío" (comportamiento actual, fail-open, contradice el Intent Envelope de LAS MANOS).
- **(e) DB no responde**: comportamiento explícito, no implícito — usa stale si está dentro del techo (con log distinguiendo `db_unreachable=1`), si no, mismo `CredentialUnavailableError` que (d), con el motivo (`DBConnectionError` vs `NoActiveCredentialError`) distinguido en el log para poder diagnosticar cuál pasó.

**Selección entre credenciales solapadas**: cuando hay 2 filas `state='active'` para el mismo `provider_id` (ventana de rotación con gracia), el resolver toma la de `activated_at` más reciente. Ambas siguen siendo válidas ante el proveedor (no se revocó la vieja), así que cualquiera serviría — se prefiere la más nueva para que el tráfico migre naturalmente hacia ella sin esperar a que la vieja se revoque explícitamente.

## 3. B1.3 — Secretos de arranque e interfaz de llave maestra

**`/etc/jax/.env` queda reducido a**: `FERNET_KEY`, `JAX_DB_HOST`, `JAX_DB_PORT`, `JAX_DB_USER`, `JAX_DB_PASSWORD`, `JAX_DB_NAME`, `JAX_JWT_SECRET`. Se conservan las 5 `*_API_KEY` durante la ventana de doble lectura de B1.4 (el fallback las necesita) — se retiran del archivo recién cuando el criterio de salida de B1.4 se cumpla, no antes. `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` quedan fuera de esta fase (no son credenciales de proveedor LLM, no tienen tabla `credential` asociada) — se listan aquí solo para que quede constancia de que no se tocan ni se justifica moverlas.

```python
class KeyProvider(ABC):
    @abstractmethod
    def get_master_key(self) -> bytes: ...

class EnvKeyProvider(KeyProvider):
    """Unica implementacion de esta fase. NO se implementa ningun KMS/Vault ahora."""
    def get_master_key(self) -> bytes:
        return os.environ["FERNET_KEY"].encode()

_key_provider: KeyProvider = EnvKeyProvider()   # unico punto de instanciacion, cambiar la implementacion aqui el dia que haya KMS
```

`crypto_secrets.py` (las 3 copias) se refactoriza: `_get_fernet()` deja de leer `os.getenv("FERNET_KEY")` directo y pasa a llamar `_key_provider.get_master_key()`. Ningún otro módulo vuelve a leer `os.environ["FERNET_KEY"]` — los 3 puntos de B0.3 quedan detrás de esta única interfaz.

**Constancia explícita, como se pidió**: **esta fase NO resuelve R2** (FERNET_KEY co-ubicada con lo que cifra). El TDE de MariaDB protege archivos en disco del datadir, pero `mariadb-dump` produce SQL en texto plano — el backup a R2 sigue conteniendo la llave y los valores cifrados en el mismo snapshot (B0.1). Lo que esta fase entrega es el punto de cambio futuro: mover a KMS/Vault el día que se decida será cambiar `_key_provider = EnvKeyProvider()` por `_key_provider = VaultKeyProvider(...)` en un solo lugar por codebase, no cazar referencias a `os.environ["FERNET_KEY"]` por todo el código. R2 se anota como deuda abierta en el registro final (CONTEXT.md), no se cierra acá.

## 4. B1.4 — Corte con doble lectura instrumentada

```python
async def resolve_credential_instrumented(provider_id: str) -> str:
    try:
        value = await resolve_credential(provider_id)          # camino DB, con cache/fail-closed de B1.2
        log.info(f"credential_resolution provider={provider_id} source=db")
        return value
    except CredentialUnavailableError:
        env_key = _PROVIDER_ENV_KEY_MAP[provider_id]            # ej. 'deepseek' -> 'DEEPSEEK_API_KEY'
        env_value = decrypt_secret(os.environ.get(env_key, ""))
        if not env_value:
            raise
        log.warning(f"credential_resolution provider={provider_id} source=env_fallback")
        return env_value
```

**Criterio de salida (medible, no una fecha arbitraria)**: **7 días consecutivos con cero líneas `source=env_fallback`** en los logs de los 4 procesos de B0.2, y esa ventana debe incluir **al menos una rotación real** ejecutada desde el admin (para probar que el camino DB efectivamente se ejercita en el escenario que más importa, no solo en lectura estable). Se mide con `journalctl | grep credential_resolution | grep env_fallback` sobre la ventana.

Si a los 7 días siguen apareciendo `env_fallback`, significa que hay un consumidor no mapeado en B0.2 (o un bug en el resolver) — se investiga ese caso puntual, **no se fuerza el corte quitando el fallback a ciegas**. Esto no es una solución temporal disfrazada de permanente: tiene una condición de salida definida y su función es medir, no tapar.

Al cumplirse el criterio: se retira `resolve_credential_instrumented` en favor de `resolve_credential` directo, y recién ahí se justifica retirar las 5 `*_API_KEY` de `/etc/jax/.env`.

## 5. B1.5 — Rollback

`user_api_keys` **no se borra, no se altera** en ningún punto de B2. Sigue siendo la tabla que consume el admin legacy (`api/admin/keys.py`) hasta que el criterio de B1.4 se cumpla — en ese momento, y como decisión aparte (fuera del alcance de esta fase, es trabajo de una fase 2), se decide si el admin legacy se retira o se deja como vista de solo lectura. Mientras tanto, si `credential`/`provider`/`credential_audit` mostraran cualquier problema, el rollback es: dejar de invocar `resolve_credential_instrumented` (revertir el código a leer `os.environ` directo, como hoy) — `user_api_keys` sigue intacta y el sistema vuelve exactamente al estado actual sin pérdida de nada, porque nunca dejó de existir.

## 6. B1.6 — Plan de pruebas (diseño; se ejecuta en B2 con evidencia real)

- [ ] **Rotar propaga sin restart**: crear una 2ª credencial `state='active'` para un proveedor de prueba vía el nuevo endpoint, sin tocar `jax-las-manos`, y confirmar en su próxima invocación real (dentro de la ventana del TTL de 30s) que usa la nueva. Evidencia: log `credential_resolution` mostrando el `credential.id` nuevo, sin ningún `systemctl restart` de por medio.
- [ ] **Revocar corta dentro del TTL declarado**: marcar `state='revoked'` en la credencial activa única (sin solapamiento) y confirmar que, pasados los 30s del TTL, la siguiente invocación falla con `CredentialUnavailableError` (o usa el stale si la DB cayó a propósito en la prueba, documentando cuál rama se ejercitó). Evidencia: timestamp de la revocación vs timestamp del primer fallo/degradación.
- [ ] **Fallo de DB → estado degradado explícito**: detener temporalmente el acceso a 12.3 (ej. bloquear el puerto con una regla de firewall de prueba, no apagar el contenedor) y confirmar que, pasado el techo de 300s de stale, la faceta se reporta degradada (no un silencio ni una llamada con credencial vieja sin avisar). Evidencia: log con `db_unreachable=1` seguido, tras el techo, de `FAIL_CLOSED`.
- [ ] **Solapamiento real**: con 2 credenciales `active` simultáneas para el mismo proveedor, confirmar con 2 invocaciones reales seguidas que ambas responden 200 del proveedor (no que una falle) — prueba de que "dos activas, ambas sirven" no es solo un estado de DB sino un comportamiento verificado contra la API real.

---

## PARADA 4

Este documento no ejecuta nada — sin `ALTER`, sin `INSERT`, sin tocar `/etc/jax/.env` todavía. Espero tu aprobación explícita antes de B2.
