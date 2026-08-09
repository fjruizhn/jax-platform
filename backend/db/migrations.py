from .connection import get_pool

CREATE_TENANTS = """
CREATE TABLE IF NOT EXISTS jax_tenants (
  tenant_id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  plan VARCHAR(20) DEFAULT 'personal',
  status VARCHAR(20) DEFAULT 'active',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_USERS = """
CREATE TABLE IF NOT EXISTS jax_users (
  user_id INT AUTO_INCREMENT PRIMARY KEY,
  tenant_id INT NOT NULL,
  email VARCHAR(100) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  role VARCHAR(20) DEFAULT 'operator',
  status VARCHAR(20) DEFAULT 'active',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (tenant_id) REFERENCES jax_tenants(tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_AXIOMA_CONFIG = """
CREATE TABLE IF NOT EXISTS axioma_config (
  config_key VARCHAR(100) PRIMARY KEY,
  config_value TEXT NOT NULL,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_AXIOMA_USAGE = """
CREATE TABLE IF NOT EXISTS axioma_usage (
  id INT AUTO_INCREMENT PRIMARY KEY,
  tenant_id INT DEFAULT 1,
  user_id INT DEFAULT 1,
  facet VARCHAR(30) NOT NULL,
  model VARCHAR(50) NOT NULL,
  tokens_in INT DEFAULT 0,
  tokens_out INT DEFAULT 0,
  cost_usd DECIMAL(10,6) DEFAULT 0,
  request_type VARCHAR(20) DEFAULT 'chat',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_AXIOMA_ARTIFACTS = """
CREATE TABLE IF NOT EXISTS axioma_artifacts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  tenant_id INT DEFAULT 1,
  user_id INT DEFAULT 1,
  name VARCHAR(200) NOT NULL,
  artifact_type VARCHAR(30) NOT NULL,
  file_path TEXT NOT NULL,
  size_bytes INT DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_PASSWORD_RESET_TOKENS = """
CREATE TABLE IF NOT EXISTS password_reset_tokens (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  token VARCHAR(36) NOT NULL UNIQUE,
  expires_at DATETIME NOT NULL,
  used BOOLEAN DEFAULT FALSE,
  created_at DATETIME DEFAULT NOW(),
  ip_address VARCHAR(45),
  FOREIGN KEY (user_id) REFERENCES jax_users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_USER_API_KEYS = """
CREATE TABLE IF NOT EXISTS user_api_keys (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL DEFAULT 1,
  provider_id VARCHAR(50) NOT NULL,
  env_key VARCHAR(100) NOT NULL,
  encrypted_value TEXT NOT NULL,
  created_at DATETIME DEFAULT NOW(),
  updated_at DATETIME DEFAULT NOW() ON UPDATE NOW(),
  UNIQUE KEY uk_user_provider (user_id, provider_id),
  FOREIGN KEY (user_id) REFERENCES jax_users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# NOTA: la exclusividad de is_active (un solo modelo activo por faceta) NO se
# aplica con un trigger — MariaDB rechaza (ERROR 1442) que un trigger
# modifique la misma tabla que lo disparó. Se aplica a nivel de aplicación
# en api/admin/facet_models.py (transacción con 2 UPDATE).
CREATE_FACET_MODELS = """
CREATE TABLE IF NOT EXISTS facet_models (
  id INT AUTO_INCREMENT PRIMARY KEY,
  facet VARCHAR(50) NOT NULL,
  provider_id VARCHAR(50) NOT NULL,
  model_name VARCHAR(100) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT FALSE,
  added_by VARCHAR(100) DEFAULT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_facet_model (facet, model_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# Fase 1 — DB como fuente de verdad para credenciales de proveedor (R3).
# Ver jax-platform/docs/fase1-credenciales-diseno.md. user_api_keys NO se
# toca — sigue siendo la red de seguridad hasta que el corte de B1.4 esté
# verificado (7 dias sin lecturas source=env_fallback).
CREATE_PROVIDER = """
CREATE TABLE IF NOT EXISTS provider (
  id VARCHAR(50) NOT NULL PRIMARY KEY,
  display_name VARCHAR(100) NOT NULL,
  base_url VARCHAR(255) NULL,
  auth_type ENUM('api_key','none','subprocess') NOT NULL,
  is_local BOOLEAN NOT NULL DEFAULT FALSE,
  status ENUM('active','deprecated') NOT NULL DEFAULT 'active',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_CREDENTIAL = """
CREATE TABLE IF NOT EXISTS credential (
  id INT AUTO_INCREMENT PRIMARY KEY,
  provider_id VARCHAR(50) NOT NULL,
  env_key VARCHAR(100) NOT NULL,
  encrypted_value TEXT NOT NULL,
  state ENUM('active','rotating','revoked') NOT NULL DEFAULT 'active',
  created_at DATETIME DEFAULT NOW(),
  activated_at DATETIME NULL,
  revoked_at DATETIME NULL,
  last_verified_at DATETIME NULL,
  last_health_status ENUM('ok','failed','unknown') NOT NULL DEFAULT 'unknown',
  last_health_detail VARCHAR(255) NULL,
  created_by INT NULL,
  FOREIGN KEY (provider_id) REFERENCES provider(id),
  FOREIGN KEY (created_by) REFERENCES jax_users(user_id),
  INDEX idx_provider_state (provider_id, state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# credential_audit es capa de aplicacion, no sustituible por el plugin de
# auditoria del servidor: ese solo ve JAX_DB_USER (unico para todo Axioma),
# nunca que superadmin humano disparo la accion — performed_by/from_ip
# vienen del JWT y del Request de FastAPI, no reconstruibles desde el log
# del servidor en ninguna version de MariaDB.
CREATE_CREDENTIAL_AUDIT = """
CREATE TABLE IF NOT EXISTS credential_audit (
  id INT AUTO_INCREMENT PRIMARY KEY,
  credential_id INT NULL,
  provider_id VARCHAR(50) NOT NULL,
  action ENUM('create','rotate','revoke','view','test') NOT NULL,
  performed_by INT NOT NULL,
  performed_from_ip VARCHAR(45) NOT NULL,
  performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  detail VARCHAR(255) NULL,
  FOREIGN KEY (credential_id) REFERENCES credential(id),
  FOREIGN KEY (performed_by) REFERENCES jax_users(user_id),
  INDEX idx_provider_time (provider_id, performed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# Fase 2 (Bloque C) — facet/facet_binding como fuente unica faceta->modelo.
# Ver jax-platform/docs/fase2-facetas-diseno.md. facet_models NO se toca
# (tabla legacy, se deja de LEER desde el codigo nuevo, mismo patron que
# user_api_keys en Fase 1).
CREATE_FACET = """
CREATE TABLE IF NOT EXISTS facet (
  `key` VARCHAR(50) NOT NULL PRIMARY KEY,
  display_name VARCHAR(100) NOT NULL,
  icon VARCHAR(10) NULL,
  color_hex VARCHAR(7) NULL,
  persona TEXT NULL,
  transport ENUM('http_openai_compat','http_gemini','motor_registry','ollama','subprocess') NOT NULL,
  requires_tool_use BOOLEAN NOT NULL DEFAULT FALSE,
  requires_structured_output BOOLEAN NOT NULL DEFAULT FALSE,
  min_context_tokens INT NOT NULL DEFAULT 0,
  max_latency_ms INT NULL,
  max_cost_per_1k_usd DECIMAL(10,6) NULL,
  auto_selectable BOOLEAN NOT NULL DEFAULT TRUE,
  status ENUM('active','degraded','disabled') NOT NULL DEFAULT 'active',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_FACET_BINDING = """
CREATE TABLE IF NOT EXISTS facet_binding (
  id INT AUTO_INCREMENT PRIMARY KEY,
  facet_key VARCHAR(50) NOT NULL,
  provider_id VARCHAR(50) NOT NULL,
  model_id VARCHAR(100) NOT NULL,
  role ENUM('primary','fallback_1','fallback_2','disabled') NOT NULL DEFAULT 'primary',
  params JSON NULL,
  approved_by INT NULL,
  approved_at DATETIME NULL,
  created_at DATETIME DEFAULT NOW(),
  FOREIGN KEY (facet_key) REFERENCES facet(`key`),
  FOREIGN KEY (provider_id) REFERENCES provider(id),
  FOREIGN KEY (approved_by) REFERENCES jax_users(user_id),
  UNIQUE KEY uk_facet_role (facet_key, role)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_TABLES = [
    ("jax_tenants", CREATE_TENANTS),
    ("jax_users", CREATE_USERS),
    ("axioma_config", CREATE_AXIOMA_CONFIG),
    ("axioma_usage", CREATE_AXIOMA_USAGE),
    ("axioma_artifacts", CREATE_AXIOMA_ARTIFACTS),
    ("password_reset_tokens", CREATE_PASSWORD_RESET_TOKENS),
    ("user_api_keys", CREATE_USER_API_KEYS),
    ("facet_models", CREATE_FACET_MODELS),
    ("provider", CREATE_PROVIDER),          # antes de credential (FK)
    ("credential", CREATE_CREDENTIAL),      # antes de credential_audit (FK)
    ("credential_audit", CREATE_CREDENTIAL_AUDIT),
    ("facet", CREATE_FACET),                # antes de facet_binding (FK)
    ("facet_binding", CREATE_FACET_BINDING),
]

# transport, requires_tool_use, auto_selectable — valores actuales reales
# (auditoria + C0), no supuestos.
_FACET_SEED = [
    # key,       display_name, icon, color,     transport,             auto_sel
    ("jax_local", "JAX Local", "🏠", "#22c55e", "ollama",              True),
    ("hyde",      "Mr. Hyde",  "🔧", "#f97316", "subprocess",          False),
    ("jekyll",    "Jekyll",    "🧪", "#6366f1", "http_openai_compat",  True),
    ("hipatia",   "Hipatia",   "📚", "#10b981", "http_gemini",         True),
    ("thot",      "Thot",      "⚖️", "#eab308", "http_openai_compat",  True),
    ("kimi",      "Kimi",      "⚡", "#06b6d4", "motor_registry",      True),
    ("ada",       "Ada",       "🏗️", "#ec4899", "http_openai_compat",  True),
]

# facet_key -> (provider_id, model_id) — modelos hoy hardcodeados en
# jacobs/executor.py (C0.2), migrados como binding role='primary' inicial.
_FACET_BINDING_SEED = [
    ("jax_local", "ollama",   "qwen3-coder:30b"),
    ("hyde",      "anthropic", "sonnet"),
    ("jekyll",    "deepseek", "deepseek-v4-flash"),
    ("hipatia",   "gemini",   "gemini-2.5-flash"),
    ("thot",      "openai",   "gpt-5.5"),
    ("kimi",      "moonshot", "kimi-k3"),
    ("ada",       "zhipu",    "glm-5.2"),
]


# Personas reales, extraidas tal cual de jacobs/executor.py (no inventadas).
# hipatia/jax_local/hyde no tienen persona estatica hoy (Gemini usa
# "contents" sin system role separado; jax_local compone su prompt con el
# nombre del modelo real inline; hyde es Claude Code, prompt propio) — NULL.
_FACET_PERSONAS = {
    "jekyll": (
        "Eres Jekyll, un analista con sensibilidad humanista. "
        "Reflexionas sobre las implicaciones humanas y sociales de los temas. "
        "Eres profundo, poético cuando es apropiado, pero siempre concreto."
    ),
    "thot": (
        "Eres Thot, el crítico de JAX. Tu trabajo es cuestionar, "
        "identificar supuestos peligrosos, riesgos ocultos y fallas de razonamiento. "
        "Sé preciso, incisivo y honesto. No seas condescendiente."
    ),
    "ada": (
        "Eres Ada, arquitecta de sistemas. "
        "Diseñas soluciones técnicas elegantes con rigor matemático."
    ),
}


async def _seed_facets(cur) -> None:
    for key, display_name, icon, color, transport, auto_sel in _FACET_SEED:
        await cur.execute(
            "INSERT IGNORE INTO facet (`key`, display_name, icon, color_hex, persona, transport, auto_selectable) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (key, display_name, icon, color, _FACET_PERSONAS.get(key), transport, auto_sel),
        )
    await cur.execute("SELECT COUNT(*) FROM facet_binding")
    (n,) = await cur.fetchone()
    if n > 0:
        return  # ya migrado — no reinsertar (idempotente fuerte, igual que credential)
    for facet_key, provider_id, model_id in _FACET_BINDING_SEED:
        await cur.execute(
            "INSERT INTO facet_binding (facet_key, provider_id, model_id, role) "
            "VALUES (%s, %s, %s, 'primary')",
            (facet_key, provider_id, model_id),
        )

# Catalogo de proveedores — reemplaza el hardcodeo de PROVIDERS en
# api/admin/keys.py:17-23. jax_local (ollama) y hyde (anthropic) se
# representan aunque no gestionen credencial via esta pantalla.
_PROVIDER_SEED = [
    # id,        display_name, base_url,                                          auth_type,     is_local
    ("openai",   "OpenAI (Thot)",     "https://api.openai.com/v1",                          "api_key",    False),
    ("deepseek", "DeepSeek (Jekyll)", "https://api.deepseek.com/v1",                         "api_key",    False),
    ("gemini",   "Gemini (Hipatia)",  "https://generativelanguage.googleapis.com/v1beta",   "api_key",    False),
    ("moonshot", "Moonshot (Kimi)",   "https://api.moonshot.ai/v1",                          "api_key",    False),
    ("zhipu",    "Z.ai (Ada)",        "https://api.z.ai/api/paas/v4",                        "api_key",    False),
    ("ollama",   "Ollama (jax_local)", "http://localhost:11434",                             "none",       True),
    ("anthropic", "Claude Code (Hyde)", None,                                                "subprocess", False),
]


async def _seed_providers(cur) -> None:
    for provider_id, display_name, base_url, auth_type, is_local in _PROVIDER_SEED:
        await cur.execute(
            """
            INSERT IGNORE INTO provider (id, display_name, base_url, auth_type, is_local)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (provider_id, display_name, base_url, auth_type, is_local),
        )


async def _migrate_user_api_keys_to_credential(cur) -> None:
    """Migracion de datos, una sola vez: si credential ya tiene filas, no
    vuelve a correr (evita duplicar en cada arranque del proceso o cada
    rotacion futura, que ya no pasa por aca)."""
    await cur.execute("SELECT COUNT(*) FROM credential")
    row = await cur.fetchone()
    if row and row[0] > 0:
        return
    await cur.execute(
        "SELECT provider_id, env_key, encrypted_value FROM user_api_keys"
    )
    rows = await cur.fetchall()
    for provider_id, env_key, encrypted_value in rows:
        await cur.execute(
            """
            INSERT INTO credential (provider_id, env_key, encrypted_value, state, activated_at)
            VALUES (%s, %s, %s, 'active', NOW())
            """,
            (provider_id, env_key, encrypted_value),
        )

_COLUMNS = [
    ("jax_users", "last_login", "ALTER TABLE jax_users ADD COLUMN last_login TIMESTAMP NULL"),
    ("jax_users", "failed_attempts", "ALTER TABLE jax_users ADD COLUMN failed_attempts INT DEFAULT 0"),
    ("jax_users", "locked_until", "ALTER TABLE jax_users ADD COLUMN locked_until DATETIME NULL"),
]


async def _table_exists(cur, table_name: str) -> bool:
    await cur.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
        """,
        (table_name,),
    )
    row = await cur.fetchone()
    return bool(row and row[0] > 0)


async def _column_exists(cur, table_name: str, column_name: str) -> bool:
    await cur.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        """,
        (table_name, column_name),
    )
    row = await cur.fetchone()
    return bool(row and row[0] > 0)


async def run_migrations():
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for table_name, ddl in _TABLES:
                if not await _table_exists(cur, table_name):
                    await cur.execute(ddl)

            for table_name, column_name, ddl in _COLUMNS:
                if not await _column_exists(cur, table_name, column_name):
                    await cur.execute(ddl)

            await _seed_providers(cur)
            await _migrate_user_api_keys_to_credential(cur)
            await _seed_facets(cur)

        await conn.commit()
