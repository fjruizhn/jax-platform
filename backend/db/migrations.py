import json
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
  model VARCHAR(100) NOT NULL,
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

CREATE_SHADOW_MESSAGES = """
CREATE TABLE IF NOT EXISTS shadow_messages (
  id INT AUTO_INCREMENT PRIMARY KEY,
  conv_uuid VARCHAR(36) NOT NULL,
  shadow_message_id CHAR(36) NOT NULL UNIQUE,
  facet VARCHAR(30) NOT NULL,
  contract_parsed BOOLEAN DEFAULT NULL,
  degradation_reason TEXT,
  has_claim BOOLEAN DEFAULT NULL,
  has_analysis BOOLEAN DEFAULT NULL,
  has_judgment BOOLEAN DEFAULT NULL,
  queued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  validated_at TIMESTAMP NULL DEFAULT NULL,
  INDEX idx_shadow_messages_facet (facet),
  INDEX idx_shadow_messages_conv_uuid (conv_uuid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_SHADOW_CLAIM_VERDICTS = """
CREATE TABLE IF NOT EXISTS shadow_claim_verdicts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  conv_uuid VARCHAR(36) NOT NULL,
  shadow_message_id CHAR(36) NOT NULL,
  predicate VARCHAR(50) NOT NULL,
  status VARCHAR(30) NOT NULL,
  detail TEXT,
  args JSON,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_shadow_claims_conv_uuid (conv_uuid),
  INDEX idx_shadow_claims_shadow_message_id (shadow_message_id),
  INDEX idx_shadow_claims_predicate (predicate)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_SHADOW_VOCAB_HITS = """
CREATE TABLE IF NOT EXISTS shadow_vocab_hits (
  id INT AUTO_INCREMENT PRIMARY KEY,
  conv_uuid VARCHAR(36) NOT NULL,
  shadow_message_id CHAR(36) NOT NULL,
  channel VARCHAR(20) NOT NULL,
  term VARCHAR(100) NOT NULL,
  category VARCHAR(50) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_shadow_vocab_conv_uuid (conv_uuid),
  INDEX idx_shadow_vocab_category (category)
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

# Fase 2 (Bloque D) — catalogo de modelos. Ver
# jax-platform/docs/fase2-facetas-diseno.md D1.1. facet_binding.model_id
# (texto libre, Bloque C) se respalda con facet_binding.model_ref (FK aqui
# abajo) durante una ventana de cutover — no se dropea en esta corrida.
CREATE_MODEL = """
CREATE TABLE IF NOT EXISTS model (
  id INT AUTO_INCREMENT PRIMARY KEY,
  provider_id VARCHAR(50) NOT NULL,
  model_id VARCHAR(100) NOT NULL,
  is_alias BOOLEAN NOT NULL DEFAULT FALSE,
  context_window INT NULL,
  supports_tool_use BOOLEAN NOT NULL DEFAULT FALSE,
  supports_structured_output BOOLEAN NOT NULL DEFAULT FALSE,
  input_modalities SET('text','image','audio','video') NOT NULL DEFAULT 'text',
  price_input_per_1m_usd DECIMAL(10,4) NULL,
  price_output_per_1m_usd DECIMAL(10,4) NULL,
  price_cache_per_1m_usd DECIMAL(10,4) NULL,
  release_date DATE NULL,
  deprecation_date DATE NULL,
  status ENUM('available','degraded','deprecated','gone') NOT NULL DEFAULT 'available',
  -- 'observed': descubierto en vivo por record_resolved_version (D1.2) --
  -- no es una de las 3 fuentes planeadas de D1.3, es una 4ta fuente real
  -- que el diseno original no prevía explicitamente (una version resuelta
  -- que aparece en una invocacion real y todavia no esta en el catalogo).
  source ENUM('provider_api','models_dev','manual','observed') NOT NULL,
  source_checked_at DATETIME NOT NULL,
  consecutive_misses INT NOT NULL DEFAULT 0,  -- D1.4: ausente en N syncs seguidos de /v1/models -> degraded/deprecated. Nunca dispara 'gone' (confirmacion manual).
  created_at DATETIME DEFAULT NOW(),
  FOREIGN KEY (provider_id) REFERENCES provider(id),
  UNIQUE KEY uk_provider_model (provider_id, model_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# Regla de oro (D1.3): el catalogo (`model`) se escribe solo via sync; un
# cambio a `facet_binding` (produccion) pasa SIEMPRE por una fila aqui
# aprobada desde el admin — el sync job jamas hace UPDATE directo a
# facet_binding.
CREATE_MODEL_BINDING_PROPOSAL = """
CREATE TABLE IF NOT EXISTS model_binding_proposal (
  id INT AUTO_INCREMENT PRIMARY KEY,
  facet_key VARCHAR(50) NOT NULL,
  current_model_ref INT NULL,
  proposed_model_ref INT NOT NULL,
  reason ENUM('new_model_available','drift_detected','deprecation_warning') NOT NULL,
  detail TEXT NULL,
  status ENUM('pending','approved','rejected') NOT NULL DEFAULT 'pending',
  decided_by INT NULL,
  decided_at DATETIME NULL,
  created_at DATETIME DEFAULT NOW(),
  FOREIGN KEY (facet_key) REFERENCES facet(`key`),
  FOREIGN KEY (proposed_model_ref) REFERENCES model(id),
  FOREIGN KEY (decided_by) REFERENCES jax_users(user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# R4 — motor desacoplado de faceta. Tres ejes separados: capability (que
# sabe hacer), transport (como se le habla, mismo enum que facet.transport),
# auth (via provider.auth_type, ya existente — ollama='none' ya sembrado).
# model_ref reusa la tabla `model` (context_window, pricing, deprecacion)
# en vez de duplicar esos campos por motor, mismo patron que
# facet_binding.model_ref.
CREATE_MOTOR = """
CREATE TABLE IF NOT EXISTS motor (
  `key` VARCHAR(50) NOT NULL PRIMARY KEY,
  model_ref INT NOT NULL,
  transport ENUM('http_openai_compat','http_gemini','motor_registry','ollama','subprocess') NOT NULL,
  max_tokens INT NULL,
  default_timeout_seconds INT NOT NULL DEFAULT 600,
  supports_reasoning BOOLEAN NOT NULL DEFAULT FALSE,
  reasoning_default_visibility ENUM('audit_only','visible') NOT NULL DEFAULT 'audit_only',
  sandbox_only BOOLEAN NOT NULL DEFAULT TRUE,
  status ENUM('active','disabled') NOT NULL DEFAULT 'active',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (model_ref) REFERENCES model(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# priority reemplaza el orden implicito de la lista allowed_motors de TOML.
# Convencion: menor priority gana primero (0 = primer intento) -- mismo
# sentido que "el primero de la lista" que _resolve_motor() ya usa.
CREATE_CAPABILITY = """
CREATE TABLE IF NOT EXISTS capability (
  `key` VARCHAR(50) NOT NULL PRIMARY KEY,
  risk_level ENUM('low','medium','high') NOT NULL,
  sandbox_only BOOLEAN NOT NULL DEFAULT TRUE,
  requires_human_gate BOOLEAN NOT NULL DEFAULT FALSE,
  max_execution_minutes INT NOT NULL,
  max_recursion_depth INT NOT NULL DEFAULT 0,
  output_schema VARCHAR(100) NULL,
  fallback_motor VARCHAR(50) NULL,
  fallback_mode ENUM('manual_only','auto') NULL,
  allowed_callers LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL CHECK (json_valid(allowed_callers)),
  forbidden_paths LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL CHECK (forbidden_paths IS NULL OR json_valid(forbidden_paths)),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (fallback_motor) REFERENCES motor(`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_CAPABILITY_MOTOR = """
CREATE TABLE IF NOT EXISTS capability_motor (
  capability_key VARCHAR(50) NOT NULL,
  motor_key VARCHAR(50) NOT NULL,
  priority INT NOT NULL DEFAULT 0,
  PRIMARY KEY (capability_key, motor_key),
  FOREIGN KEY (capability_key) REFERENCES capability(`key`),
  FOREIGN KEY (motor_key) REFERENCES motor(`key`)
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
    ("shadow_messages", CREATE_SHADOW_MESSAGES),
    ("shadow_claim_verdicts", CREATE_SHADOW_CLAIM_VERDICTS),
    ("shadow_vocab_hits", CREATE_SHADOW_VOCAB_HITS),
    ("provider", CREATE_PROVIDER),          # antes de credential y model (FK)
    ("credential", CREATE_CREDENTIAL),      # antes de credential_audit (FK)
    ("credential_audit", CREATE_CREDENTIAL_AUDIT),
    ("model", CREATE_MODEL),                # antes de model_binding_proposal (FK)
    ("facet", CREATE_FACET),                # antes de facet_binding y model_binding_proposal (FK)
    ("facet_binding", CREATE_FACET_BINDING),
    ("model_binding_proposal", CREATE_MODEL_BINDING_PROPOSAL),
    ("motor", CREATE_MOTOR),                          # antes de capability (FK fallback_motor)
    ("capability", CREATE_CAPABILITY),                # antes de capability_motor (FK)
    ("capability_motor", CREATE_CAPABILITY_MOTOR),
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

# Portado de ~/jax/las_manos/config.toml [motors.*] (2026-08-18). model_ref
# se resuelve por SELECT en vez de hardcodear el id -- el AUTO_INCREMENT de
# `model` no es estable entre instalaciones.
_MOTOR_SEED = [
    # key,   provider_id, model_id,   transport,             max_tokens, timeout, reasoning, visibility,    sandbox
    ("kimi", "moonshot", "kimi-k3",   "http_openai_compat",  8000,       600,     True,      "audit_only",  True),
    ("ada",  "zhipu",    "glm-5.2",   "http_openai_compat",  8000,       600,     True,      "audit_only",  True),
]

# key, risk_level, sandbox_only, requires_human_gate, max_exec_min, max_recursion,
# output_schema, fallback_motor, fallback_mode, allowed_callers, forbidden_paths
_CAPABILITY_SEED = [
    ("code_swarm", "high", True, True, 30, 1, "code_swarm.v1", "ada", "manual_only",
     ["hyde", "ada", "kimi", "jacobs"], [".env", "secrets/", "private_keys/", "credentials/"]),
    ("refactor", "medium", True, False, 10, 0, "code_patch.v1", None, None,
     ["hyde", "ada", "jacobs"], None),
    ("architecture_review", "medium", True, False, 5, 0, "architecture_review.v1", None, None,
     ["hyde", "jacobs"], None),
    ("bug_hunt", "high", True, True, 15, 0, "bug_hunt.v1", None, None,
     ["hyde", "ada", "jacobs"], None),
    ("pipeline_analysis", "low", True, False, 15, 0, "analysis.v1", None, None,
     ["jacobs", "hyde"], None),
    ("implementation", "medium", True, False, 30, 0, "code_patch.v1", None, None,
     ["jacobs", "hyde"], [".env", "secrets/", "private_keys/", "credentials/"]),
    ("generate", "low", True, False, 15, 0, "generate.v1", None, None,
     ["jacobs", "hyde", "ada"], None),
    ("reason", "low", True, False, 15, 0, "reason.v1", None, None,
     ["jacobs", "hyde", "ada", "thot"], None),
    ("design", "low", True, False, 15, 0, "design.v1", None, None,
     ["jacobs", "hyde", "ada"], None),
    ("validate_consistency", "low", True, False, 15, 0, "validation.v1", None, None,
     ["jacobs", "hyde", "thot"], None),
    ("reconcile", "low", True, False, 15, 0, "reconcile.v1", None, None,
     ["jacobs", "hyde", "ada"], None),
    ("critique", "low", True, False, 15, 0, "critique.v1", None, None,
     ["jacobs", "hyde", "thot"], None),
]

# (capability_key, [motor_key, ...] en orden de prioridad). "thot" queda
# excluido a proposito de validate_consistency/critique -- no existe como
# motor todavia (Task 8 lo crea junto con esas 2 filas via INSERT directo,
# el criterio de aceptacion #4). Sin esto, la FK de capability_motor
# rompe el seed.
_CAPABILITY_MOTOR_SEED = [
    ("code_swarm", ["kimi"]),
    ("refactor", ["kimi"]),
    ("architecture_review", ["ada"]),
    ("bug_hunt", ["kimi"]),
    ("pipeline_analysis", ["kimi"]),
    ("implementation", ["kimi"]),
    ("generate", ["kimi", "ada"]),
    ("reason", ["ada", "kimi"]),
    ("design", ["ada", "kimi"]),
    ("validate_consistency", ["ada"]),  # "thot" excluido, ver nota arriba
    ("reconcile", ["ada", "kimi"]),
    ("critique", ["ada"]),              # "thot" excluido, ver nota arriba
]


async def _seed_motors_and_capabilities(cur) -> None:
    for key, provider_id, model_id, transport, max_tokens, timeout, reasoning, visibility, sandbox in _MOTOR_SEED:
        await cur.execute(
            "SELECT id FROM model WHERE provider_id=%s AND model_id=%s",
            (provider_id, model_id),
        )
        row = await cur.fetchone()
        if row is None:
            # model no sembrado todavia (orden de _seed_models_and_backfill) --
            # no romper el seed completo por un motor que se puede agregar despues.
            continue
        model_ref = row[0]
        await cur.execute(
            "INSERT IGNORE INTO motor "
            "(`key`, model_ref, transport, max_tokens, default_timeout_seconds, "
            " supports_reasoning, reasoning_default_visibility, sandbox_only) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (key, model_ref, transport, max_tokens, timeout, reasoning, visibility, sandbox),
        )

    for (key, risk_level, sandbox_only, gate, max_exec, max_rec, schema,
         fallback_motor, fallback_mode, callers, forbidden) in _CAPABILITY_SEED:
        await cur.execute(
            "INSERT IGNORE INTO capability "
            "(`key`, risk_level, sandbox_only, requires_human_gate, max_execution_minutes, "
            " max_recursion_depth, output_schema, fallback_motor, fallback_mode, "
            " allowed_callers, forbidden_paths) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (key, risk_level, sandbox_only, gate, max_exec, max_rec, schema,
             fallback_motor, fallback_mode, json.dumps(callers),
             json.dumps(forbidden) if forbidden is not None else None),
        )

    for capability_key, motor_keys in _CAPABILITY_MOTOR_SEED:
        for priority, motor_key in enumerate(motor_keys):
            await cur.execute(
                "SELECT 1 FROM motor WHERE `key`=%s",
                (motor_key,),
            )
            if await cur.fetchone() is None:
                continue  # motor no existe todavia -- no romper el seed (ver nota _CAPABILITY_MOTOR_SEED)
            await cur.execute(
                "INSERT IGNORE INTO capability_motor (capability_key, motor_key, priority) "
                "VALUES (%s, %s, %s)",
                (capability_key, motor_key, priority),
            )


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


# provider_id -> (api_key_transport, models_list_url). Los 4 OpenAI-
# compatibles + Gemini (query_param, ya visto en api/admin/keys.py:159-166)
# usan `credential` DB via el transport indicado. anthropic Y ollama tienen
# sync real pero NINGUNO de los dos usa `credential`/transport de esta
# tabla — model_catalog.py los resuelve aparte, ver sus ramas explicitas en
# sync_provider_models: anthropic via el token OAuth local de Claude Code
# (~/.claude/.credentials.json, decision explicita de no implementar refresh
# OAuth propio — riesgo de romper la sesion en vivo de Hyde, ver CONTEXT.md
# 2026-08-10), ollama sin ninguna auth (provider.auth_type='none', local).
# Ambas URLs verificadas con curl real (2026-08-10), no inventadas.
_PROVIDER_SYNC_SEED = [
    ("openai",    "header_bearer", "https://api.openai.com/v1/models"),
    ("deepseek",  "header_bearer", "https://api.deepseek.com/v1/models"),
    ("moonshot",  "header_bearer", "https://api.moonshot.ai/v1/models"),
    ("zhipu",     "header_bearer", "https://api.z.ai/api/paas/v4/models"),
    ("gemini",    "query_param",   "https://generativelanguage.googleapis.com/v1beta/models"),
    ("anthropic", "header_bearer", "https://api.anthropic.com/v1/models"),
    # ollama: local, sin API key (provider.auth_type='none') — transport
    # queda en el default inerte, model_catalog.py nunca lo lee para este
    # provider (bypassa credencial/headers por completo). URL real
    # verificada con curl (2026-08-10): GET /api/tags, sin auth.
    ("ollama",    "header_bearer", "http://localhost:11434/api/tags"),
]


async def _seed_provider_sync_config(cur) -> None:
    """Idempotente pero NO 'set once + nunca tocar': el guard es
    models_list_url IS NULL, para no pisar un valor editado a mano despues
    (D1.5 no expone edicion de esta columna en la UI todavia, pero el guard
    ya queda correcto para cuando exista)."""
    for provider_id, transport, url in _PROVIDER_SYNC_SEED:
        await cur.execute(
            "UPDATE provider SET api_key_transport=%s, models_list_url=%s "
            "WHERE id=%s AND models_list_url IS NULL",
            (transport, url, provider_id),
        )


async def _seed_models_and_backfill(cur) -> None:
    """D1.1 — deriva el catalogo inicial de los bindings YA migrados en
    Bloque C (_seed_facets), no de una lista nueva inventada. source='manual'
    a proposito: todavia no corrio ningun sync real contra el proveedor
    (eso es D1.3/model_catalog.py, deliberadamente separado del arranque).
    is_alias=False: ninguno de los 7 bindings actuales usa un puntero movil
    (serian estilo 'deepseek-chat'); todos son versiones fijadas verificadas
    en Bloque C0. INSERT IGNORE + UPDATE...WHERE model_ref IS NULL: seguro
    de re-correr, nunca duplica ni pisa un binding ya resuelto a mano."""
    await cur.execute("SELECT DISTINCT provider_id, model_id FROM facet_binding")
    pairs = await cur.fetchall()
    for provider_id, model_id in pairs:
        await cur.execute(
            "INSERT IGNORE INTO model (provider_id, model_id, is_alias, status, source, source_checked_at) "
            "VALUES (%s, %s, FALSE, 'available', 'manual', NOW())",
            (provider_id, model_id),
        )
    await cur.execute(
        "UPDATE facet_binding b "
        "JOIN model m ON m.provider_id = b.provider_id AND m.model_id = b.model_id "
        "SET b.model_ref = m.id "
        "WHERE b.model_ref IS NULL"
    )


async def _fix_anthropic_sonnet_alias(cur) -> None:
    """Correccion puntual (2026-08-10): _seed_models_and_backfill sembro
    anthropic/sonnet con is_alias=FALSE junto a los otros 6 bindings de
    Bloque C0, pero 'sonnet' es un alias de tier (no una version fijada
    tipo 'claude-sonnet-4-5-20250929') — confirmado contra el catalogo real
    de GET /v1/models (curl, 2026-08-10). Guard is_alias=FALSE: corrige una
    vez, no pisa una edicion manual futura.

    Ademas repara el efecto colateral real del primer sync de anthropic
    (mismo dia, mismo hallazgo): /v1/models jamas lista 'sonnet' suelto —
    solo los IDs fechados detras del alias — asi que ese primer sync lo
    marco 'degraded' (consecutive_misses=1) por una ausencia estructural,
    no una senal real. model_catalog.sync_provider_models ya excluye los
    alias de tier de anthropic del conteo de misses desde este fix — esto
    solo repara el estado que quedo mal ANTES de que ese fix existiera.
    Guard status='degraded': no pisa un 'deprecated'/'gone' real posterior
    de otra causa."""
    await cur.execute(
        "UPDATE model SET is_alias=TRUE "
        "WHERE provider_id='anthropic' AND model_id='sonnet' AND is_alias=FALSE"
    )
    await cur.execute(
        "UPDATE model SET status='available', consecutive_misses=0 "
        "WHERE provider_id='anthropic' AND model_id='sonnet' AND status='degraded'"
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
    # Bloque D (D1.1/D1.3) — divergencia real ya presente en
    # api/admin/keys.py:158-169 (Gemini usa ?key=, los otros 4 Authorization:
    # Bearer). models_list_url NULL = sin sync automatico de capa (a)
    # todavia para ese provider (ollama/anthropic).
    ("provider", "api_key_transport", "ALTER TABLE provider ADD COLUMN api_key_transport ENUM('header_bearer','query_param') NOT NULL DEFAULT 'header_bearer'"),
    ("provider", "models_list_url", "ALTER TABLE provider ADD COLUMN models_list_url VARCHAR(255) NULL"),
    # Bloque D (D1.1) — FK real contra `model`; `model_id` (texto libre,
    # Bloque C) se conserva de solo-lectura durante el cutover, no se dropea
    # en esta corrida.
    ("facet_binding", "model_ref", "ALTER TABLE facet_binding ADD COLUMN model_ref INT NULL, ADD CONSTRAINT fk_facet_binding_model_ref FOREIGN KEY (model_ref) REFERENCES model(id)"),
    # Bloque D (D1.2) — detector de drift: lo que el proveedor confirma
    # haber ejecutado, capturado del campo `model` de la respuesta.
    ("facet_binding", "resolved_version", "ALTER TABLE facet_binding ADD COLUMN resolved_version VARCHAR(100) NULL"),
    ("facet_binding", "resolved_version_checked_at", "ALTER TABLE facet_binding ADD COLUMN resolved_version_checked_at DATETIME NULL"),
    # Cubre entornos donde `model` ya se creo antes de agregar esta columna
    # al DDL de arriba (ej. esta misma corrida de verificacion) — mismo
    # patron que jax_users.last_login.
    ("model", "consecutive_misses", "ALTER TABLE model ADD COLUMN consecutive_misses INT NOT NULL DEFAULT 0"),
    # Ollama local sync (2026-08-10): un tag (`qwen3-coder:30b`) es un
    # puntero LOCAL, no un alias del proveedor — puede re-pullearse con
    # pesos distintos sin que el tag cambie, algo que los otros transportes
    # no tienen forma de detectar. `digest` (de /api/tags, verificado con
    # curl real) es la unica senal de eso. digest_changed_at queda NULL
    # hasta la primera vez que se observa un cambio real (nunca en la
    # primera vez que se ve el modelo -- no hay "antes" con que comparar).
    ("model", "digest", "ALTER TABLE model ADD COLUMN digest VARCHAR(80) NULL"),
    ("model", "digest_changed_at", "ALTER TABLE model ADD COLUMN digest_changed_at DATETIME NULL"),
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


async def _enum_has_value(cur, table_name: str, column_name: str, value: str) -> bool:
    await cur.execute(
        """
        SELECT COLUMN_TYPE FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """,
        (table_name, column_name),
    )
    row = await cur.fetchone()
    return bool(row) and f"'{value}'" in row[0]


# (tabla, columna, valor nuevo, ALTER MODIFY completo) — ensancha un ENUM
# existente sin tocar los valores ya presentes. Ver 'observed' en D1.2:
# fuente real que el diseno original de D1.3 no preveia.
_ENUM_EXTENSIONS = [
    (
        "model", "source", "observed",
        "ALTER TABLE model MODIFY COLUMN source "
        "ENUM('provider_api','models_dev','manual','observed') NOT NULL",
    ),
]


async def _column_too_narrow(cur, table_name: str, column_name: str, min_length: int) -> bool:
    await cur.execute(
        """
        SELECT CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """,
        (table_name, column_name),
    )
    row = await cur.fetchone()
    return bool(row) and row[0] is not None and row[0] < min_length


# (tabla, columna, longitud minima requerida, ALTER MODIFY completo) —
# ensancha una columna VARCHAR existente sin perder datos. Instalaciones
# nuevas ya nacen con el ancho correcto via CREATE TABLE; esto cubre las
# que ya tenian la tabla creada con el ancho viejo.
# axioma_usage.model era VARCHAR(50) pero model.model_id (el valor real
# insertado desde Tarea 2/3) es VARCHAR(100) — riesgo de "Data too long"
# bajo STRICT_TRANS_TABLES, silenciado hasta ahora por el bare except de
# record_usage (ver I1).
_COLUMN_WIDENS = [
    (
        "axioma_usage", "model", 100,
        "ALTER TABLE axioma_usage MODIFY COLUMN model VARCHAR(100) NOT NULL",
    ),
]


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

            for table_name, column_name, value, ddl in _ENUM_EXTENSIONS:
                if not await _enum_has_value(cur, table_name, column_name, value):
                    await cur.execute(ddl)

            for table_name, column_name, min_length, ddl in _COLUMN_WIDENS:
                if await _column_too_narrow(cur, table_name, column_name, min_length):
                    await cur.execute(ddl)

            await _seed_providers(cur)
            await _migrate_user_api_keys_to_credential(cur)
            await _seed_facets(cur)
            await _seed_provider_sync_config(cur)
            await _seed_models_and_backfill(cur)
            await _fix_anthropic_sonnet_alias(cur)
            await _seed_motors_and_capabilities(cur)

        await conn.commit()
