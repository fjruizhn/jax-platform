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

# axioma_artifacts DROPEADA (Bloque 2, 2026-08-21): tabla mas vieja del
# repo (commit ed7719a7d4, 2026-06-19), 0 filas, 0 writers, 0 readers en
# ambos repos, confirmado contra CONTEXT.md:340 y la DB real. La feature
# que la motivaba (scoping multi-tenant de artifacts) se resolvio por otro
# camino: AdminRepository.jsx escanea el filesystem en vivo (REPO_BASE=
# ~/jax/repo, os.stat()), no necesita esta tabla. DDL original preservada
# aca por si la decision se revierte -- ver _drop_axioma_artifacts() abajo:
#
# CREATE TABLE IF NOT EXISTS axioma_artifacts (
#   id INT AUTO_INCREMENT PRIMARY KEY,
#   tenant_id INT DEFAULT 1,
#   user_id INT DEFAULT 1,
#   name VARCHAR(200) NOT NULL,
#   artifact_type VARCHAR(30) NOT NULL,
#   file_path TEXT NOT NULL,
#   size_bytes INT DEFAULT 0,
#   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
# ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

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
  -- allowed_callers: ALCANCE ACOTADO, leer antes de editar esta columna.
  -- Gobierna SOLO a los callers que no tienen concepto de `capability`.
  -- Hoy eso es exactamente uno: 'jax_platform_chat' (Mesa web,
  -- backend/api/chat.py::_invoke_facet -> POST /motor/authorize-facet ->
  -- check_facet_admission(), repo jax, las_manos/motor_registry/
  -- facet_policy.py).
  --
  -- JACOBS NO SE GOBIERNA ACÁ. Jacobs pasa por
  -- `capability.allowed_callers` vía MotorPolicy.check_capability_admission()
  -- (repo jax, las_manos/motor_registry/policy.py), invocado desde
  -- jacobs/executor.py::validate_capability(). Consecuencia práctica, y la
  -- razón por la que este comentario existe: SACAR "jacobs" DE ESTA COLUMNA
  -- NO RESTRINGE A JACOBS -- va a seguir despachando igual, sin error ni
  -- aviso. Para cortarle el acceso hay que editar
  -- `capability.allowed_callers` de las capabilities involucradas.
  -- El "jacobs" sembrado abajo (_seed_http_facet_allowed_callers) es
  -- descriptivo (refleja el acceso que ya existía de hecho), no ejecutivo.
  -- Follow-up candidato registrado en DEUDA.md: hacer que Jacobs también
  -- consulte check_facet_admission(), para que esta columna pase a ser el
  -- gate real de nivel facet para AMBOS caminos y deje de enseñar un
  -- modelo mental equivocado.
  allowed_callers LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL CHECK (allowed_callers IS NULL OR json_valid(allowed_callers)),
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
  -- max_tokens_param: nombre del parametro de limite de salida que exige la
  -- API de ESTE modelo. Otro descriptor del contrato por modelo, mismo eje que
  -- supports_tool_use / supports_structured_output / context_window. NULL a
  -- proposito (sin DEFAULT): ver _seed_model_max_tokens_param() y el comentario
  -- de _COLUMNS mas abajo.
  max_tokens_param ENUM('max_tokens','max_completion_tokens') NULL,
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
  disable_reasoning BOOLEAN NOT NULL DEFAULT TRUE,
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
  -- VESTIGIAL (ver DEUDA.md). Ningun lector en el codigo real compara este
  -- valor contra nada. El sandbox_only que SI se enforce es
  -- motor.sandbox_only -- columna DISTINTA, tabla motor, chequeada en
  -- las_manos/motor_registry/policy.py (check 7 de MotorPolicy.check()).
  --
  -- QUE SE VERIFICO, EXACTAMENTE (2026-08-27, repos jax + jax-platform):
  --   grep -rn "cap\.sandbox_only|capability\.sandbox_only|
  --             entry\[.sandbox_only.\]|entry\.get\(.sandbox_only"
  --   --include="*.py"   ->  0 resultados.
  -- La columna SI se carga desde la DB (jacobs/store.py y
  -- motor_registry/catalog.py la leen hacia CapabilityEntry.sandbox_only),
  -- pero ese atributo nunca se compara ni se ramifica en ningun lado.
  -- Al momento de verificar, las 5 filas relevantes (research, analysis,
  -- design, reconcile, validate_consistency) tenian el valor 1.
  --
  -- QUE **NO** SE VERIFICO: por que existe, que se penso que significara,
  -- ni si algun consumidor externo a estos dos repos la lee. No se le
  -- invento semantica a proposito -- el candidato obvio (acotar egress de
  -- red) es un item de deuda diferido aparte, no una decision que este
  -- cierre podia tomar. Si la encontras dentro de dos años: lo probado es
  -- que ningun codigo Python de estos dos repos la consultaba en esa
  -- fecha, nada mas. Pendiente: darle lector real o dropearla.
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
# max_execution_minutes realineado 2026-08-20 (pago de deuda, ronda 3, T1
# paso 1/3): el campo se carga (catalog.py) pero ningun timeout real lo
# consume todavia (ni MotorPolicy.check() ni el executor de Jacobs -- ver
# CONTEXT.md) -- este cambio es solo dato, cero riesgo de produccion hoy.
# Valores puestos a 5 (300s, el default real que ya corre en produccion via
# jacobs/plan.py::_DEFAULT_TIMEOUT_SECONDS) donde la evidencia real (jacobs_
# steps.started_at/finished_at + las_manos/logs/motor_jobs.jsonl, 2026-08-20)
# no respalda un valor mayor -- code_swarm/bug_hunt/pipeline_analysis: CERO
# corridas reales encontradas (ni en Jacobs ni en Motor Registry), sin
# evidencia para 30/15/15; refactor: 4 corridas reales (motor_jobs.jsonl),
# max 34.5s, muy por debajo de 300s; implementation/generate/
# validate_consistency/critique: corridas reales en jacobs_steps, max
# observado 215.3s/8.5s/107.5s/124.1s respectivamente, ninguna cerca de
# 300s. design/reason quedan en 15 (900s) SIN CAMBIO -- evidencia real de
# 2 fallos genuinos exactos en el techo de 300s (jacobs_steps: 1 step
# 'design' y 1 'reconcile' 'reason' failed a dur=300.0s=timeout_seconds),
# mismo patron que el incidente que ya justifico subir 'reconcile' a 900s.
# reconcile sin cambio (ya validado, ver tests/test_jacobs_timeout_by_
# capability.py). architecture_review sin cambio (ya en 5, cero evidencia
# en contra). Enforcer sigue sin cablear -- este commit NO cambia
# comportamiento de produccion, solo hace que el dato deje de mentir.
_CAPABILITY_SEED = [
    ("code_swarm", "high", True, True, 5, 1, "code_swarm.v1", "ada", "manual_only",
     ["hyde", "ada", "kimi", "jacobs"], [".env", "secrets/", "private_keys/", "credentials/"]),
    ("refactor", "medium", True, False, 5, 0, "code_patch.v1", None, None,
     ["hyde", "ada", "jacobs"], None),
    ("architecture_review", "medium", True, False, 5, 0, "architecture_review.v1", None, None,
     ["hyde", "jacobs"], None),
    ("bug_hunt", "high", True, True, 5, 0, "bug_hunt.v1", None, None,
     ["hyde", "ada", "jacobs"], None),
    ("pipeline_analysis", "low", True, False, 5, 0, "analysis.v1", None, None,
     ["jacobs", "hyde"], None),
    ("implementation", "medium", True, False, 5, 0, "code_patch.v1", None, None,
     ["jacobs", "hyde"], [".env", "secrets/", "private_keys/", "credentials/"]),
    ("generate", "low", True, False, 5, 0, "generate.v1", None, None,
     ["jacobs", "hyde", "ada"], None),
    ("reason", "low", True, False, 15, 0, "reason.v1", None, None,
     ["jacobs", "hyde", "ada", "thot"], None),
    ("design", "low", True, False, 15, 0, "design.v1", None, None,
     ["jacobs", "hyde", "ada"], None),
    ("validate_consistency", "low", True, False, 5, 0, "validation.v1", None, None,
     ["jacobs", "hyde", "thot"], None),
    ("reconcile", "low", True, False, 15, 0, "reconcile.v1", None, None,
     ["jacobs", "hyde", "ada"], None),
    ("critique", "low", True, False, 5, 0, "critique.v1", None, None,
     ["jacobs", "hyde", "thot"], None),
    # research, analysis, review were hand-seeded into production jax_memory at
    # some point but never added to the idempotent migration list (same pattern
    # as the 'depends_on' column bug documented in DEUDA.md). Fresh databases
    # (test, dev, disaster recovery) never received them. These are HTTP-direct
    # capabilities (no Motor Registry entries) dispatched to hipatia/jekyll/thot/ada.
    ("research", "low", True, False, 5, 0, None, None, None,
     ["jacobs"], None),
    ("analysis", "low", True, False, 5, 0, None, None, None,
     ["jacobs"], None),
    ("review", "medium", True, False, 5, 0, None, None, None,
     ["jacobs"], None),
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
        effective_fallback_motor = fallback_motor
        if fallback_motor is not None:
            await cur.execute(
                "SELECT 1 FROM motor WHERE `key`=%s",
                (fallback_motor,),
            )
            if await cur.fetchone() is None:
                # motor de fallback no existe todavia -- no romper el seed
                # completo por una FK (mismo criterio que el guard de
                # capability_motor mas abajo).
                effective_fallback_motor = None
        await cur.execute(
            "INSERT IGNORE INTO capability "
            "(`key`, risk_level, sandbox_only, requires_human_gate, max_execution_minutes, "
            " max_recursion_depth, output_schema, fallback_motor, fallback_mode, "
            " allowed_callers, forbidden_paths) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (key, risk_level, sandbox_only, gate, max_exec, max_rec, schema,
             effective_fallback_motor, fallback_mode, json.dumps(callers),
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


async def _seed_jax_local_motor(cur) -> None:
    """R4 Task 4: Qwen (jax_local) como motor real, no atado a la faceta
    conversacional. provider.base_url de ollama se corrige a incluir /v1 --
    ningun codigo lo consumia hasta ahora (chat.py::_call_ollama usa el
    formato nativo de Ollama, no este base_url), asi que es seguro.
    Compite por capabilities de razonamiento/generacion (generate, reason,
    design, reconcile), no por code_swarm/refactor/bug_hunt/implementation
    (agentico de alto riesgo, hoy exclusivo de Kimi) -- decision de dato,
    ajustable despues sin tocar codigo."""
    await cur.execute(
        "UPDATE provider SET base_url='http://localhost:11434/v1' "
        "WHERE id='ollama' AND (base_url IS NULL OR base_url = '')"
    )
    await cur.execute(
        "SELECT id FROM model WHERE provider_id='ollama' AND model_id='qwen3-coder:30b'"
    )
    row = await cur.fetchone()
    if row is None:
        return
    await cur.execute(
        "INSERT IGNORE INTO motor "
        "(`key`, model_ref, transport, max_tokens, default_timeout_seconds, "
        " supports_reasoning, reasoning_default_visibility, sandbox_only) "
        "VALUES ('jax_local', %s, 'ollama', 0, 300, FALSE, 'audit_only', TRUE)",
        (row[0],),
    )
    for capability_key, priority in [("generate", 2), ("reason", 2), ("design", 2), ("reconcile", 2)]:
        await cur.execute(
            "INSERT IGNORE INTO capability_motor (capability_key, motor_key, priority) "
            "VALUES (%s, 'jax_local', %s)",
            (capability_key, priority),
        )


async def _seed_jax_local_has_tool_access(cur) -> None:
    """T1 (2026-08-21): backfill idempotente de has_tool_access para
    instalaciones donde la fila `motor` de jax_local ya existia antes de
    que la columna se agregara (ALTER ... DEFAULT FALSE no la marca sola).
    UPDATE sin condicion de "solo si NULL" a proposito: correr esto de
    nuevo con jax_local ya en TRUE es un no-op idempotente, no un riesgo."""
    await cur.execute("UPDATE motor SET has_tool_access=TRUE WHERE `key`='jax_local'")


async def _seed_file_tools_capabilities(cur) -> None:
    """GAP2 Fase2 (2026-08-19, jax/las_manos/motor_registry/tool_authority.py):
    capabilities dedicadas para read_file/write_file -- ninguna de las 12
    capabilities existentes mapea honestamente a "leer/escribir un archivo"
    (verificado real, SELECT contra jax_memory: solo code_swarm/
    implementation tienen forbidden_paths poblado, y ninguna de las dos
    lista jax_local en capability_motor; generate/reason/design/reconcile
    SI listan jax_local pero tienen forbidden_paths=NULL -- reusarlas
    hubiera dejado read_file sin proteccion real de .env/secrets/).

    Ajustado por Fernando antes de aprobar el seed: file_read en
    risk_level='medium' (no 'low') -- leer archivos arbitrarios del
    workspace es acceso a datos que el modelo no tenia, forbidden_paths
    cubre lo conocido, no lo que todavia no esta en la lista.

    max_execution_minutes=1 en ambas originalmente (2026-08-19): placeholder
    deliberado, honesto para cuando se cablee, sin riesgo porque nada lo
    lee. Recalibrado a 5 (300s) el 2026-08-20 (pago de deuda ronda 3, T1
    paso 1/3) por instrucción directa de Fernando -- alinear con el default
    real que ya corre en produccion (jacobs/plan.py::_DEFAULT_TIMEOUT_
    SECONDS=300) en vez de con un placeholder sin evidencia. Sigue sin
    consumir ningun timeout real hoy (enforcer sin cablear, ver
    CONTEXT.md) -- este cambio tampoco altera comportamiento de produccion.

    forbidden_paths reutiliza EXACTO el mismo array ya usado por
    code_swarm/implementation -- no una lista nueva paralela.
    allowed_callers=['jacobs']: unico caller real (GAP2 Fase1, gate
    literal de motor=='jax_local' en worker.py, siempre despachado como
    caller='jacobs')."""
    file_capabilities = [
        # key, risk_level, sandbox_only, requires_human_gate, max_execution_minutes,
        # max_recursion_depth, output_schema, fallback_motor, fallback_mode, callers, forbidden
        ("file_read", "medium", True, False, 5, 0, "", None, None,
         ["jacobs"], [".env", "secrets/", "private_keys/", "credentials/"]),
        # T3 (Fase4, 2026-08-19): requires_human_gate False -- ver
        # _fix_file_write_no_human_gate() abajo, que ademas actualiza la
        # fila si ya existia sembrada con True (produccion real, sembrada
        # en Fase2 antes de esta decision).
        ("file_write", "medium", True, False, 5, 0, "", None, None,
         ["jacobs"], [".env", "secrets/", "private_keys/", "credentials/"]),
    ]
    for (key, risk_level, sandbox_only, gate, max_exec, max_rec, schema,
         fallback_motor, fallback_mode, callers, forbidden) in file_capabilities:
        await cur.execute(
            "INSERT IGNORE INTO capability "
            "(`key`, risk_level, sandbox_only, requires_human_gate, max_execution_minutes, "
            " max_recursion_depth, output_schema, fallback_motor, fallback_mode, "
            " allowed_callers, forbidden_paths) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (key, risk_level, sandbox_only, gate, max_exec, max_rec, schema,
             fallback_motor, fallback_mode, json.dumps(callers), json.dumps(forbidden)),
        )

    await cur.execute("SELECT 1 FROM motor WHERE `key`='jax_local'")
    if await cur.fetchone() is None:
        return  # motor no existe todavia -- no romper el seed (mismo guard que _seed_jax_local_motor)
    for capability_key in ("file_read", "file_write"):
        await cur.execute(
            "INSERT IGNORE INTO capability_motor (capability_key, motor_key, priority) "
            "VALUES (%s, 'jax_local', 0)",
            (capability_key,),
        )
    # Ronda 7 (2026-08-20, T4.b): kimi agregado como motor alternativo
    # (priority 1, detras de jax_local) -- kimi ya esta en _MOTOR_FACETS
    # (jacobs/executor.py), ya tiene fila completa en `motor` (sandbox_only,
    # transport http_openai_compat), y es el motor agentico designado para
    # tareas de codigo (comentario existente: code_swarm/refactor/bug_hunt/
    # implementation son "hoy exclusivo de Kimi"). Aditivo puro: no quita el
    # binding de jax_local, no toca executor.py, no afecta a ada/thot.
    await cur.execute("SELECT 1 FROM motor WHERE `key`='kimi'")
    if await cur.fetchone() is not None:
        for capability_key in ("file_read", "file_write"):
            await cur.execute(
                "INSERT IGNORE INTO capability_motor (capability_key, motor_key, priority) "
                "VALUES (%s, 'kimi', 1)",
                (capability_key,),
            )


async def _seed_thot_motor(cur) -> None:
    """R4 -- criterio de aceptacion decisivo del spec: motor nuevo dado de
    alta SOLO por dato (INSERT), sin tocar worker.py/catalog.py (ya
    generalizados por transport en Tasks 2-3). openai/credential ya estan
    activos -- los usa Thot del lado de la Mesa, cero setup nuevo.
    validate_consistency/critique referenciaban 'thot' en config.toml
    (allowed_motors) pero Task 1 excluyo esas 2 filas porque el motor no
    existia -- se completan aca."""
    await cur.execute(
        "SELECT id FROM model WHERE provider_id='openai' AND model_id='gpt-5.5'"
    )
    row = await cur.fetchone()
    if row is None:
        return
    await cur.execute(
        "INSERT IGNORE INTO motor "
        "(`key`, model_ref, transport, max_tokens, default_timeout_seconds, "
        " supports_reasoning, reasoning_default_visibility, sandbox_only) "
        "VALUES ('thot', %s, 'http_openai_compat', 0, 300, FALSE, 'audit_only', TRUE)",
        (row[0],),
    )
    for capability_key in ("validate_consistency", "critique"):
        await cur.execute(
            "INSERT IGNORE INTO capability_motor (capability_key, motor_key, priority) "
            "VALUES (%s, 'thot', 0)",
            (capability_key,),
        )
        # ada ya tenia priority=0 (Task 1) -- bajarla a 1 para no empatar
        # con thot, sin tocar la fila de thot recien insertada.
        await cur.execute(
            "UPDATE capability_motor SET priority=1 "
            "WHERE capability_key=%s AND motor_key='ada' AND priority=0",
            (capability_key,),
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


async def _fix_file_write_gate_and_auditor(cur) -> None:
    """GAP2 Fase4 (2026-08-19): file_write se sembro en Fase2 con
    requires_human_gate=True. T3 de esta sesion lo cambia a False --
    reencuadre de diseno: el gate no desaparece, se mueve de "aprobacion
    previa" a "jail + forbidden_paths + git (rollback exacto) + auditoria
    posterior por otra faceta" (ver tool_authority.py, worker.py, CONTEXT.md).

    Verificado ANTES de tocar la columna que ningun caller real dispatcha
    con capability='file_write' como capability de TOPE (grep sobre
    jacobs/*.py y las_manos/**/*.py, cero resultados fuera de
    tool_authority.py/tests) -- el segundo consumidor real de
    requires_human_gate (motor_registry/policy.py::MotorPolicy.check(),
    gate de DISPATCH top-level, distinto del gate de tool_authority.py
    sobre cada tool_call) existe pero nunca se ejercita para esta
    capability en el codigo real: tool_authority.py resuelve autoridad por
    tool_name de forma independiente, sin confiar en la capability top-level
    del job. Guard UPDATE ... WHERE requires_human_gate=TRUE: corrige una
    vez, no pisa una reversion manual futura a True si alguien la quisiera.

    auditor_motor='thot': default (T4) -- GPT-5.5, transporte/proveedor
    distinto de jax_local (el unico productor de tool_calls hoy), evita
    auto-revision. Guard WHERE auditor_motor IS NULL: no pisa una
    configuracion manual posterior."""
    await cur.execute(
        "UPDATE capability SET requires_human_gate=FALSE "
        "WHERE `key`='file_write' AND requires_human_gate=TRUE"
    )
    await cur.execute(
        "UPDATE capability SET auditor_motor='thot' "
        "WHERE `key`='file_write' AND auditor_motor IS NULL"
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


async def _drop_axioma_artifacts(cur) -> None:
    """Bloque 2 (2026-08-21): axioma_artifacts confirmada huerfana -- 0
    filas, 0 writers, 0 readers en ambos repos (ver comentario junto al
    DDL preservado, arriba de CREATE_PASSWORD_RESET_TOKENS). Idempotente:
    no falla en instalaciones que ya la dropearon o que nunca la crearon."""
    if await _table_exists(cur, "axioma_artifacts"):
        await cur.execute("DROP TABLE axioma_artifacts")


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
    # Gobernanza de _HTTP_FACETS (docs/superpowers/specs/2026-08-27-
    # http-facets-motor-policy-governance-design.md): hipatia/jekyll/thot/ada
    # quedan con allowed_callers poblado, kimi/jax_local/hyde quedan NULL.
    # Consumida por check_facet_admission() (repo jax, Task 4).
    ("facet", "allowed_callers",
     "ALTER TABLE facet ADD COLUMN allowed_callers LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL "
     "CHECK (allowed_callers IS NULL OR json_valid(allowed_callers))"),
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
    # 2026-08-27 (incidente thot, 3 dias caido en la Mesa web): la API de
    # gpt-5.6-terra rechaza con HTTP 400 el parametro que _call_openai_compat
    # mandaba fijo -- "Unsupported parameter: 'max_tokens' is not supported with
    # this model. Use 'max_completion_tokens' instead". El nombre del parametro
    # de limite de salida dejo de ser universal: es una propiedad estable POR
    # MODELO, del mismo eje que supports_tool_use/context_window, y por eso vive
    # en `model` y no en una constante del despachador (cambiar la constante al
    # nombre nuevo arregla thot y rompe jekyll/deepseek-v4-flash y ada/glm-5.3,
    # que siguen exigiendo el viejo).
    #
    # Se descarto explicitamente el fallback por error de la API (reintentar con
    # el otro nombre al ver el 400): gasta una llamada fallida cada vez para
    # descubrir algo que es una propiedad estable del modelo, y su modo de falla
    # se confunde con un error real de la API.
    #
    # NULL, sin DEFAULT, es la decision deliberada: un modelo sin valor FALLA
    # RUIDOSO en el dispatch (api/chat.py::_max_tokens_field) en vez de asumir.
    # Si el default fuera el parametro viejo, el proximo modelo nuevo se romperia
    # igual que thot pero en silencio. Nace con lector (_max_tokens_field, via
    # facet_resolver.ResolvedFacet.max_tokens_param) y con test
    # (tests/test_model_max_tokens_param.py) -- no repite el destino de
    # capability.sandbox_only, declarada vestigial el mismo dia por no haber
    # tenido lector nunca.
    ("model", "max_tokens_param",
     "ALTER TABLE model ADD COLUMN max_tokens_param ENUM('max_tokens','max_completion_tokens') NULL"),
    ("model", "digest", "ALTER TABLE model ADD COLUMN digest VARCHAR(80) NULL"),
    ("model", "digest_changed_at", "ALTER TABLE model ADD COLUMN digest_changed_at DATETIME NULL"),
    # T2 (2026-08-19, jax/las_manos/motor_registry/worker.py): "disable
    # reasoning por defecto" NO es aplicable parejo entre proveedores --
    # verificado real contra las 3 APIs: Ollama acepta reasoning_effort=none
    # (funciona), Moonshot/Kimi lo RECHAZA con 400 ("only type=enabled is
    # allowed for this model" -- kimi-k2.7-code no permite desactivar su
    # razonamiento), Zhipu/Ada lo ignora en silencio (200 pero
    # reasoning_content sigue poblado -- necesitaria su propio parametro,
    # no verificado, no wireado esta ronda). Por eso vive en `motor`, no un
    # flag global: es una propiedad de que API tiene detras cada motor, no
    # una politica pareja. DEFAULT TRUE = off salvo que el motor declare lo
    # contrario; el dispatch solo emite la senal real cuando
    # transport='ollama' (unico camino verificado) -- para los demas el
    # valor de esta columna no tiene efecto todavia, documentado en worker.py.
    ("motor", "disable_reasoning", "ALTER TABLE motor ADD COLUMN disable_reasoning BOOLEAN NOT NULL DEFAULT TRUE"),
    # GAP2 Fase4 (2026-08-19, tool_authority.py write_file): "quien audita"
    # es propiedad de la CAPABILITY (mismo eje que risk_level/
    # requires_human_gate -- gobernanza de la operacion, no del motor que la
    # ejecuto), no del motor ni de una tabla nueva. NULL = auditoria
    # desactivada para esa capability (default explicito, no implicito).
    # Override real por request via context={"auditor": "<motor>"|false},
    # resuelto en worker.py -- esta columna es solo el default.
    ("capability", "auditor_motor", "ALTER TABLE capability ADD COLUMN auditor_motor VARCHAR(50) NULL"),
    # T1 (2026-08-21, diagnostico pipeline 19ad2c42-cdf): has_tool_access
    # vivia SOLO como `if motor == "jax_local"` en worker.py:488 -- nada
    # podia preguntarle al sistema que motor ejecuta tools, y el frontend
    # (PipelineModal.jsx) pedia /motors/capabilities y lo descartaba,
    # armando el plan con un mapa hardcodeado en su lugar (causa raiz del
    # incidente). Va en `motor`, no en `capability_motor`: es propiedad de
    # que API/gobernanza hay detras de cada motor (mismo eje que
    # sandbox_only/disable_reasoning, no de que capability se ejecuta --
    # capability_motor ya goberna ESO por separado, y kimi tiene filas ahi
    # para file_write pese a no tener tools). DEFAULT FALSE preserva el
    # comportamiento actual para todo motor existente sin tocar codigo --
    # jax_local se marca TRUE explicitamente en _seed_jax_local_has_tool_access.
    ("motor", "has_tool_access", "ALTER TABLE motor ADD COLUMN has_tool_access BOOLEAN NOT NULL DEFAULT FALSE"),
    # P0 (2026-08-22, auditoria usage_writer): record_motor_usage()/
    # record_direct_usage() solo corrian en la rama de EXITO -- un job que
    # fallaba (timeout, error de schema, cualquier cosa) nunca escribia fila,
    # aunque gasto tokens reales contra una API paga (confirmado: 7/9 jobs
    # reales de Motor Registry en la ventana auditada, sin fila). `status`
    # distingue el desenlace -- un token gastado en un fallo es tan real como
    # uno en un exito, pero importa saber cual fue para diagnosticar. NULL
    # para filas viejas (nunca tuvieron este dato, no hay como reconstruirlo).
    ("axioma_usage", "status", "ALTER TABLE axioma_usage ADD COLUMN status VARCHAR(20) NULL"),
    # job_id: sin esto, reconciliar axioma_usage contra motor_jobs.jsonl
    # (la fuente de verdad de LAS MANOS) exige matchear por timestamp+facet,
    # aproximado. Con job_id, el join es exacto -- lo que T3 (chequeo de
    # reconciliacion) necesita para no dar falsos positivos.
    ("axioma_usage", "job_id", "ALTER TABLE axioma_usage ADD COLUMN job_id VARCHAR(36) NULL"),
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


async def _eliminate_motor_model_ref_denormalization(cur) -> None:
    """2026-08-24 -- el bug de divergencia motor/facet_binding (ada glm-5.2
    vs glm-5.3, thot gpt-5.5 vs gpt-5.6-terra en produccion) volvio a
    aparecer 5 dias despues de que el incidente de 2026-08-19 se "cerro"
    con sync de dos escrituras en approve_proposal() (models.py) + un
    guard de rechazo en update_motor() (motors.py). Auditoria completa de
    la superficie de escritura (todo UPDATE/INSERT sobre ambas tablas en
    jax + jax-platform, incluyendo scripts/migraciones/tests) encontro que
    ninguno de esos dos mecanismos cubria PUT /api/admin/facet-bindings/
    {key} (facet_bindings.py::update_facet_binding) -- el camino que de
    hecho se uso para ada (2026-08-22) y thot (2026-08-24): cero filas en
    model_binding_proposal para ninguno de los dos, así que nunca pasaron
    por el sync. Se encontro ademas un CUARTO camino sin guardar
    (create_motor() podia sembrar un motor homonimo de una faceta con su
    propio model_ref, sin chequeo de colision).

    Fix real, no un guard mas por endpoint: motor.model_ref deja de ser
    una fuente independiente de identidad para las claves de motor que
    tienen una faceta homonima (hoy: ada/jax_local/kimi/thot -- el 100%
    de las filas de `motor` existentes; ver auditoria, 0 motores sin
    faceta homonima). La vista motor_resolved resuelve SIEMPRE por
    facet_binding.model_ref cuando existe un binding role='primary' para
    esa clave (via COALESCE, facet_binding gana), y cae a motor.model_ref
    (ahora NULLABLE) solo para un motor genuinamente independiente --
    ninguno existe hoy, pero create_motor()/update_motor() lo siguen
    contemplando sin cambios. Todo lector de identidad de modelo (list_motors
    en jax-platform, MotorCatalog.from_db() en las_manos) pasa a leer esta
    vista, no la tabla motor cruda.

    Por que esto sí es "impossible by construction" y el fix de 2026-08-19
    no lo era: un escritor futuro (un 5o camino, uno que ni sabemos que va
    a existir) puede seguir escribiendo motor.model_ref para 'ada' sin que
    nada se lo impida -- pero ese valor queda en una columna que la vista
    JAMAS lee para esa fila. No hace falta que el escritor "se entere" de
    facet_binding, ni que alguien le agregue un guard cuando se escriba.
    La divergencia deja de ser observable por construccion, no por
    disciplina de cada endpoint.

    kimi tiene approved_by/approved_at NULL (nunca paso por el flujo de
    aprobacion humana, se sembro directo en Bloque D1.1 2026-08-09) -- es
    metadata de procedencia, no afecta la resolucion: la vista usa
    facet_binding.model_ref sin mirar approved_at.

    hipatia/jekyll/hyde tienen fila en facet_binding pero NINGUNA fila en
    motor hoy (confirmado con LEFT JOIN, 0 filas) -- el LEFT JOIN de la
    vista no rompe para ellos, simplemente no producen fila en
    motor_resolved (no tienen motor que resolver). Si algun dia se les da
    de alta un motor homonimo (decision pendiente del item _HTTP_FACETS de
    DEUDA.md, no parte de este fix), la vista ya los cubre sin cambios."""
    await cur.execute("ALTER TABLE motor MODIFY model_ref INT NULL")
    await cur.execute(
        "UPDATE motor m "
        "JOIN facet_binding fb ON fb.facet_key = m.`key` AND fb.role = 'primary' "
        "SET m.model_ref = NULL "
        "WHERE m.model_ref IS NOT NULL"
    )
    await cur.execute("""
        CREATE OR REPLACE VIEW motor_resolved AS
        SELECT
            mo.`key`,
            COALESCE(fb.model_ref, mo.model_ref) AS model_ref,
            mo.transport,
            mo.max_tokens,
            mo.default_timeout_seconds,
            mo.supports_reasoning,
            mo.reasoning_default_visibility,
            mo.sandbox_only,
            mo.status,
            mo.created_at,
            mo.disable_reasoning,
            mo.has_tool_access
        FROM motor mo
        LEFT JOIN facet_binding fb ON fb.facet_key = mo.`key` AND fb.role = 'primary'
    """)


# (provider_id, model_id, max_tokens_param) — verificado contra la DB en vivo
# (jax_memory, 2026-08-27), no supuesto:
#   - gpt-5.6-terra  (thot,   provider openai)   -> RECHAZA 'max_tokens' con
#     HTTP 400, exige 'max_completion_tokens'. Es el modelo del incidente.
#   - deepseek-v4-flash (jekyll, provider deepseek) -> 'max_tokens', funcionando
#     hoy en produccion.
#   - glm-5.3        (ada,    provider zhipu)    -> 'max_tokens', funcionando
#     hoy en produccion.
# Los tres pasan HOY por _call_openai_compat: sembrar solo el de thot tumbaria
# jekyll y ada (que no estan rotos) en cuanto NULL empiece a fallar ruidoso.
#   - kimi-k3        (motor kimi, provider moonshot) -> 'max_tokens'. NO pasa por
#     _call_openai_compat: despacha por las_manos/motor_registry/worker.py, otro
#     repo, que hoy manda 'max_tokens' fijo. Se siembra por completitud del
#     catalogo (la columna describe el modelo, no el despachador que lo usa);
#     ese repo queda intacto y no lee la columna todavia.
_MODEL_MAX_TOKENS_PARAM_SEED = [
    ("openai",   "gpt-5.6-terra",      "max_completion_tokens"),
    ("deepseek", "deepseek-v4-flash",  "max_tokens"),
    ("zhipu",    "glm-5.3",            "max_tokens"),
    ("moonshot", "kimi-k3",            "max_tokens"),
]


async def _seed_model_max_tokens_param(cur) -> None:
    """Siembra model.max_tokens_param para los modelos cuyo contrato esta
    verificado (ver _MODEL_MAX_TOKENS_PARAM_SEED).

    Deliberadamente NO siembra el resto del catalogo: un valor adivinado por
    proveedor es exactamente la suposicion que esta columna existe para
    eliminar. Un modelo que no esta en esta lista queda NULL y hace fallar el
    dispatch con un mensaje que dice que fila sembrar
    (api/chat.py::_max_tokens_field) -- ruidoso por diseno.

    Guard WHERE max_tokens_param IS NULL: idempotente y no pisa un valor puesto
    a mano si un operador ya sembro algo distinto (mismo patron que
    _seed_http_facet_allowed_callers)."""
    for provider_id, model_id, param in _MODEL_MAX_TOKENS_PARAM_SEED:
        await cur.execute(
            "UPDATE model SET max_tokens_param = %s "
            "WHERE provider_id = %s AND model_id = %s AND max_tokens_param IS NULL",
            (param, provider_id, model_id),
        )


async def _seed_http_facet_allowed_callers(cur) -> None:
    """Gobernanza de _HTTP_FACETS (docs/superpowers/specs/2026-08-27-
    http-facets-motor-policy-governance-design.md): hipatia/jekyll/thot/
    ada quedan con allowed_callers=["jacobs","jax_platform_chat"] --
    mismo acceso que ya existia informalmente (ninguno de los dos estaba
    bloqueado antes de esta ronda), ahora explicito. kimi/jax_local/hyde
    quedan NULL a proposito -- fuera de alcance esta ronda, fail-closed
    por diseno (ver facet_policy.py::check_facet_admission en el repo jax).

    Guard WHERE allowed_callers IS NULL: no pisa un valor manual futuro
    si alguien ya lo configuro distinto."""
    await cur.execute(
        "UPDATE facet SET allowed_callers = %s "
        "WHERE `key` IN ('hipatia','jekyll','thot','ada') AND allowed_callers IS NULL",
        (json.dumps(["jacobs", "jax_platform_chat"]),),
    )


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

            await _drop_axioma_artifacts(cur)
            await _seed_providers(cur)
            await _migrate_user_api_keys_to_credential(cur)
            await _seed_facets(cur)
            await _seed_provider_sync_config(cur)
            await _seed_models_and_backfill(cur)
            await _fix_anthropic_sonnet_alias(cur)
            await _seed_motors_and_capabilities(cur)
            await _seed_jax_local_motor(cur)
            await _seed_jax_local_has_tool_access(cur)
            await _seed_thot_motor(cur)
            await _seed_file_tools_capabilities(cur)
            await _fix_file_write_gate_and_auditor(cur)
            await _eliminate_motor_model_ref_denormalization(cur)
            await _seed_http_facet_allowed_callers(cur)
            # Despues de _seed_models_and_backfill: las filas de `model` tienen
            # que existir para poder actualizarlas.
            await _seed_model_max_tokens_param(cur)

        await conn.commit()
