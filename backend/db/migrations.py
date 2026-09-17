import json
import logging
import os
from pathlib import Path

import aiomysql

import ajustes

from .connection import get_pool

logger = logging.getLogger(__name__)

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
  email VARCHAR(320) NOT NULL UNIQUE,
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

# Migraciones de DATOS que corren una sola vez (frente C, 2026-09-16). Las de
# esquema son idempotentes por inspección; una de datos que fija valores no lo
# es: correrla en cada arranque pisaría lo que el admin cambió después.
CREATE_AXIOMA_MIGRACION_DE_DATOS = """
CREATE TABLE IF NOT EXISTS axioma_migracion_de_datos (
  nombre VARCHAR(100) PRIMARY KEY,
  aplicada_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

# NOTA: la exclusividad de is_active (un solo modelo activo por faceta) la
# aplicaba el router legado de facet_models, borrado el 2026-09-16 (frente A,
# A-34); la tabla queda como dato histórico sin escritor.
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
  grounding_snapshot LONGTEXT NULL,
  grounding_snapshot_sha256 CHAR(64) NULL,
  -- 2026-09-03: texto CRUDO tal como lo emitió el modelo, acotado por bytes
  -- (shadow_validation.py::_RAW_COLUMN_BYTES). Es el ÚNICO lugar donde queda
  -- el bloque `analysis` -- donde el modelo explica por qué eligió el
  -- puntero que eligió -- que hasta hoy no se persiste en ningún lado. Sin
  -- eso no se puede auditar una citación equivocada (POINTER_MISMATCH /
  -- FACT_NOT_IN_SNAPSHOT, ver _reclassify_provenance_mismatch mas abajo):
  -- se ve QUE se equivocó, pero no el razonamiento con el que se equivocó.
  contract_raw LONGTEXT NULL,
  -- 2026-09-03: origen declarado por quien LLAMA (web/probe/test), nunca
  -- inferido. El grano es el TURNO, no la conversación -- una conversación
  -- es de larga vida y puede mezclar orígenes (una sonda y un uso real en
  -- la misma hebra), un turno no: cada POST /chat es un origen y solo uno.
  -- Default 'unattributed' DELIBERADO: la ausencia de declaración no es
  -- evidencia de uso orgánico. Un llamador que no declara su origen se
  -- cuenta como no atribuido, nunca como real -- lo contrario sería
  -- fail-open (una sonda que se olvida de marcarse contaminaría la
  -- muestra haciéndose pasar por tráfico real). Medido el mismo día: sin
  -- esta columna, una fila de sonda y una de uso real eran idénticas en
  -- la base (mismo source, user_id, project_id, request_type) y solo se
  -- podían distinguir por un hecho de la sesión (la hora en que se corrió
  -- la sonda), no de la base.
  origin VARCHAR(20) NOT NULL DEFAULT 'unattributed',
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
  authority VARCHAR(12) NULL,
  evidence_pointer VARCHAR(100) NULL,
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
  -- max_output_tokens: cuantos tokens de SALIDA acepta como maximo la API de
  -- ESTE modelo. Par del campo de arriba: max_tokens_param dice COMO se llama
  -- el parametro, max_output_tokens dice QUE VALOR admite. Hecho distinto de
  -- context_window (que es la ventana TOTAL, entrada+salida): gpt-5.6-terra
  -- tiene context_window=1050000 y un tope de completion de 128000, asi que el
  -- segundo NO se deriva del primero. NULL a proposito (sin DEFAULT): ver
  -- _seed_model_max_output_tokens() y el comentario de _COLUMNS mas abajo.
  max_output_tokens INT NULL,
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
  FOREIGN KEY (decided_by) REFERENCES jax_users(user_id),
  -- PR-L ronda 2: list_proposals ordena por created_at, con o sin status.
  INDEX idx_created (created_at),
  INDEX idx_status_created (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# PR-L (2026-09-14, Ruling 33) — auditoria del catalogo de modelos. Dos
# eventos, los dos de un superadmin humano (performed_by/from_ip del JWT y del
# Request: el log del servidor solo ve JAX_DB_USER, mismo motivo que
# credential_audit):
#   - 'contrato_declarado': PUT /api/admin/models/{id}/contrato-dispatch cambio
#     max_tokens_param/max_output_tokens de la fila. valor_antes/valor_despues
#     son el par completo, asi un contrato mal declarado se revierte leyendo
#     esta tabla y no la memoria de nadie.
#   - 'binding_rechazado': el guard de contrato_dispatch rechazo (409) aprobar
#     una propuesta (proposal_id) o un PUT de binding (proposal_id NULL).
#     valor_despues guarda el `detail` del 409 tal cual lo vio el admin.
# Por que no credential_audit: su action es un ENUM de credenciales y su
# provider_id/credential_id no describen una fila de `model`. Por que no
# columnas en model_binding_proposal: el rechazo tambien pasa en el PUT (sin
# propuesta) y una columna guarda solo el ULTIMO -- esto es historia.
# Indices: idx_proposal_id -> ultimo rechazo de cada propuesta por
# (proposal_id, id); idx_facet_rechazo -> ultimo rechazo de cada faceta
# (action, facet_key, MAX(id)) en la pantalla de bindings; idx_model_time ->
# historia de una fila.
#
# SIN FK a model ni a model_binding_proposal (ronda 1 de PR-L, 2026-09-14,
# decision del coordinador con la autorizacion de Fernando): una auditoria no
# puede impedir borrar la entidad que audita, ni perder su fila si se borra.
# model_ref/proposal_id quedan como el numero que eran; provider_id/model_id
# (y facet_key) guardan los identificadores LEGIBLES del momento del evento,
# asi la historia se entiende aunque la fila ya no exista.
# Ronda 2: tampoco FK a jax_users. performed_by queda como el numero que era
# y performed_by_email guarda el email del momento: borrar a quien actuo no
# falla ni borra su historia. (credential_audit, facet_binding.approved_by y
# model_binding_proposal.decided_by siguen bloqueando un DELETE de usuario:
# preexistente, va a DEUDA con la baja logica de la etapa 5.) Una base con
# una forma anterior la convierte _auditoria_de_catalogo_sin_fk_duras().
CREATE_MODEL_CATALOG_AUDIT = """
CREATE TABLE IF NOT EXISTS model_catalog_audit (
  id INT AUTO_INCREMENT PRIMARY KEY,
  action ENUM('contrato_declarado','binding_rechazado') NOT NULL,
  model_ref INT NOT NULL,
  provider_id VARCHAR(50) NULL,
  model_id VARCHAR(100) NULL,
  facet_key VARCHAR(50) NULL,
  proposal_id INT NULL,
  code VARCHAR(64) NULL,
  valor_antes LONGTEXT NULL CHECK (valor_antes IS NULL OR json_valid(valor_antes)),
  valor_despues LONGTEXT NULL CHECK (valor_despues IS NULL OR json_valid(valor_despues)),
  performed_by INT NOT NULL,
  performed_by_email VARCHAR(255) NULL,
  performed_from_ip VARCHAR(45) NOT NULL,
  performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_proposal_id (proposal_id, id),
  INDEX idx_facet_rechazo (action, facet_key, id),
  INDEX idx_model_time (model_ref, performed_at)
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
CREATE_CAPABILITY = r"""
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
  -- Tanda A v3 (2026-09-14, decisión de Fernando): ¿la capability cambia el
  -- estado del sistema? 'mutating' solo file_write; el resto produce texto o
  -- parches sin aplicarlos. VARCHAR(16) + CHECK, NOT NULL y SIN DEFAULT a
  -- propósito: un ENUM NOT NULL sin default NO daba la garantía que este
  -- diseño quería -- medido en MariaDB 12.3.3: omitir la columna guarda en
  -- silencio el primer valor del ENUM ('read_only'), el mismo fail-open que
  -- se quería evitar. Con VARCHAR(16)+CHECK, omitir `mode` falla con 1364
  -- (STRICT_TRANS_TABLES) y un valor fuera del conjunto falla con 4025.
  -- Lo lee MotorCatalog.from_db() (jax) y lo verifica el resolver de
  -- CAPABILITY_AVAILABLE. Spec jax 2026-09-14-gobernanza-catalogo-db-design.md
  -- §0 (v3) y §3.1.
  mode VARCHAR(16) NOT NULL,
  CONSTRAINT chk_capability_mode CHECK (mode IN ('read_only','mutating')),
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

CREATE_FACET_HEALTH_EVENT = """
CREATE TABLE IF NOT EXISTS facet_health_event (
    id      BIGINT AUTO_INCREMENT PRIMARY KEY,
    facet   VARCHAR(50) NOT NULL,
    outcome ENUM('ok','provider_error','gate_denied','gate_unreachable',
                 'unbound','unsupported_transport','probe_error',
                 'config_error') NOT NULL,
    source  ENUM('chat','canary_periodic','canary_rebind','preflight') NOT NULL,
    detail  VARCHAR(255) NULL,
    ts      DOUBLE NOT NULL,
    KEY idx_facet_ts (facet, ts),
    KEY idx_ts (ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# `ts` es epoch DOUBLE, no TIMESTAMP: misma decision y misma razon que
# jacobs_events.ts -- inmune a la timezone de sesion. La limpieza de
# axioma_usage del 2026-08-21 perdio 90 de 106 filas comparando un
# TIMESTAMP contra un string de fecha.

CREATE_FACET_HEALTH_ALERT = """
CREATE TABLE IF NOT EXISTS facet_health_alert (
    facet         VARCHAR(50) PRIMARY KEY,
    state         ENUM('ok','down','unknown') NOT NULL,
    first_seen_ts DOUBLE NOT NULL,
    notified_ts   DOUBLE NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# facet_health_alert NO es una segunda fuente de verdad de salud: es el
# registro de que ya se aviso. La salud se calcula exclusivamente desde
# facet_health_event. La clave centinela '__system__' guarda el estado de
# la alerta agregada (sonda entera caida) para que pase por la MISMA
# supresion que las de facet -- sin eso, una sonda muerta un viernes
# produce un mensaje por barrido, 288 el sabado.

# Registro de acciones de administración de usuarios (2026-09-12, admin
# usuarios etapa 3, spec §3.3). Sin FK a jax_users a propósito: el historial
# sobrevive a lo que le pase a la fila (y la baja no borra filas).
CREATE_USER_ADMIN_AUDIT = """
CREATE TABLE IF NOT EXISTS user_admin_audit (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  ts DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  actor_user_id INT NOT NULL,
  target_user_id INT NOT NULL,
  action VARCHAR(40) NOT NULL,
  detail JSON NULL,
  ip VARCHAR(45) NULL,
  INDEX idx_user_admin_audit_target_ts (target_user_id, ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# Auditoría del kill switch (2026-09-16, frente B). Una fila por CAMBIO real
# del freno (poner o quitar), no por pedido. Sin FK a jax_users, como
# user_admin_audit: la historia sobrevive a la baja del usuario. `at` en UTC
# explícito (UTC_TIMESTAMP(6)); el último cambio sale por idx_kill_switch_audit_at.
CREATE_KILL_SWITCH_AUDIT = """
CREATE TABLE IF NOT EXISTS kill_switch_audit (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  accion VARCHAR(10) NOT NULL,
  user_id INT NOT NULL,
  at DATETIME(6) NOT NULL,
  CONSTRAINT chk_kill_switch_audit_accion CHECK (accion IN ('activar', 'reanudar')),
  INDEX idx_kill_switch_audit_at (at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# Ejecutor SP1 (plan 1, 2026-09-17). Catálogos que no crecen (inventario y reglas:
# decenas de filas) y una tabla que sí (puntos de restauración, con índice para la
# única consulta que la lee: el último verificado por máquina).
CREATE_EJECUTOR_HOST = """
CREATE TABLE IF NOT EXISTS ejecutor_host (
  nombre VARCHAR(50) NOT NULL PRIMARY KEY,
  ip VARCHAR(45) NOT NULL,
  puerto INT NOT NULL,
  rol ENUM('hypervisor','desarrollo','produccion','clientes','respaldo') NOT NULL,
  es_local BOOLEAN NOT NULL DEFAULT FALSE,
  machine_id CHAR(32) NULL,
  con_datos_de_clientes BOOLEAN NOT NULL DEFAULT TRUE,
  sudo BOOLEAN NOT NULL DEFAULT FALSE,
  api_only BOOLEAN NOT NULL DEFAULT FALSE,
  activo BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME DEFAULT NOW(),
  UNIQUE KEY uk_ejecutor_host_ip_puerto (ip, puerto)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_EJECUTOR_REGLA = """
CREATE TABLE IF NOT EXISTS ejecutor_regla (
  id INT AUTO_INCREMENT PRIMARY KEY,
  codigo VARCHAR(80) NOT NULL,
  tipo ENUM('prohibido','destructivo') NOT NULL,
  herramientas VARCHAR(200) NOT NULL,
  campo ENUM('command','file_path','cualquiera') NOT NULL,
  patron VARCHAR(1000) NOT NULL,
  ambito_host VARCHAR(50) NULL,
  ambito_roles SET('hypervisor','desarrollo','produccion','clientes','respaldo') NULL,
  es_canario BOOLEAN NOT NULL DEFAULT FALSE,
  activa BOOLEAN NOT NULL DEFAULT TRUE,
  origen VARCHAR(300) NOT NULL,
  ejemplos_coincide JSON NOT NULL,
  ejemplos_no_coincide JSON NOT NULL,
  created_at DATETIME DEFAULT NOW(),
  UNIQUE KEY uk_ejecutor_regla_codigo (codigo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CREATE_EJECUTOR_PUNTO_RESTAURACION = """
CREATE TABLE IF NOT EXISTS ejecutor_punto_restauracion (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  host_nombre VARCHAR(50) NOT NULL,
  referencia VARCHAR(255) NOT NULL,
  metodo VARCHAR(50) NOT NULL,
  restaurado_y_verificado_at DATETIME NOT NULL COMMENT 'UTC: momento en que se RESTAURÓ y verificó, no en que se respaldó',
  verificado_por VARCHAR(100) NOT NULL,
  evidencia VARCHAR(500) NOT NULL,
  created_at DATETIME DEFAULT NOW(),
  INDEX idx_ejecutor_punto_host_fecha (host_nombre, restaurado_y_verificado_at),
  FOREIGN KEY (host_nombre) REFERENCES ejecutor_host(nombre)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_TABLES = [
    ("jax_tenants", CREATE_TENANTS),
    ("jax_users", CREATE_USERS),
    ("axioma_config", CREATE_AXIOMA_CONFIG),
    ("axioma_migracion_de_datos", CREATE_AXIOMA_MIGRACION_DE_DATOS),
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
    ("model_catalog_audit", CREATE_MODEL_CATALOG_AUDIT),  # sin FK (PR-L rondas 1-2): el orden no importa
    ("motor", CREATE_MOTOR),                          # antes de capability (FK fallback_motor)
    ("capability", CREATE_CAPABILITY),                # antes de capability_motor (FK)
    ("capability_motor", CREATE_CAPABILITY_MOTOR),
    ("facet_health_event", CREATE_FACET_HEALTH_EVENT),
    ("facet_health_alert", CREATE_FACET_HEALTH_ALERT),
    ("user_admin_audit", CREATE_USER_ADMIN_AUDIT),    # sin FK a propósito
    ("kill_switch_audit", CREATE_KILL_SWITCH_AUDIT),  # sin FK a propósito
    ("ejecutor_host", CREATE_EJECUTOR_HOST),                            # antes de punto_restauracion (FK)
    ("ejecutor_regla", CREATE_EJECUTOR_REGLA),
    ("ejecutor_punto_restauracion", CREATE_EJECUTOR_PUNTO_RESTAURACION),
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
    ("kimi",      "Kimi",      "⚡", "#06b6d4", "http_openai_compat",  True),
    ("ada",       "Ada",       "🏗️", "#ec4899", "http_openai_compat",  True),
]

# facet_key -> (provider_id, model_id) — modelos hoy hardcodeados en
# jacobs/executor.py (C0.2), migrados como binding role='primary' inicial.
_FACET_BINDING_SEED = [
    ("jax_local", "ollama",   "qwen3-coder:30b"),
    ("hyde",      "anthropic", "sonnet"),
    ("jekyll",    "deepseek", "deepseek-v4-flash"),
    ("hipatia",   "gemini",   "gemini-2.5-flash"),
    # thot y ada (PR-L ronda 1, 2026-09-14, hallazgo de PR-K): gpt-5.5 y
    # glm-5.2 no tienen contrato de dispatch en _MODEL_MAX_*_SEED, así que en
    # una base VACÍA esas facetas nacían rotas (http_openai_compat falla
    # cerrado sin max_tokens_param/max_output_tokens). Ahora son los modelos
    # de producción según este mismo archivo (ver los comentarios de
    # _MODEL_MAX_*_SEED), que sí lo tienen. Solo afecta a una base vacía:
    # _seed_facets no escribe bindings si la tabla ya tiene filas.
    # tests/test_semilla_contrato_dispatch.py es el tripwire.
    ("thot",      "openai",   "gpt-5.6-terra"),
    ("kimi",      "moonshot", "kimi-k3"),
    ("ada",       "zhipu",    "glm-5.3"),
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
    # max_tokens 0 (D1 de Fernando, spec 2026-09-17 §1): sin tope propio, manda
    # model.max_output_tokens. Los 8000 cortaron el pipeline ef9b2d6e.
    ("kimi", "moonshot", "kimi-k3",   "http_openai_compat",  0,          600,     True,      "audit_only",  True),
    # glm-5.3 (PR-L ronda 1): el mismo modelo que el binding semilla de ada.
    # Con glm-5.2, en una base vacía la fila de `model` ya no existe (se
    # deriva de los bindings) y _seed_motors_and_capabilities salteaba el
    # motor ada en silencio.
    ("ada",  "zhipu",    "glm-5.3",   "http_openai_compat",  0,          600,     True,      "audit_only",  True),
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
    # generate 5 -> 15 min el 2026-09-12: ver _raise_generate_execution_ceiling.
    ("generate", "low", True, False, 15, 0, "generate.v1", None, None,
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

# GAP2 Fase2 (2026-08-19, jax/las_manos/motor_registry/tool_authority.py):
# capabilities dedicadas para read_file/write_file -- ninguna de las 12
# capabilities existentes mapea honestamente a "leer/escribir un archivo"
# (verificado real, SELECT contra jax_memory: solo code_swarm/
# implementation tienen forbidden_paths poblado, y ninguna de las dos
# lista jax_local en capability_motor; generate/reason/design/reconcile
# SI listan jax_local pero tienen forbidden_paths=NULL -- reusarlas
# hubiera dejado read_file sin proteccion real de .env/secrets/).
#
# Ajustado por Fernando antes de aprobar el seed: file_read en
# risk_level='medium' (no 'low') -- leer archivos arbitrarios del
# workspace es acceso a datos que el modelo no tenia, forbidden_paths
# cubre lo conocido, no lo que todavia no esta en la lista.
#
# max_execution_minutes=1 en ambas originalmente (2026-08-19): placeholder
# deliberado, honesto para cuando se cablee, sin riesgo porque nada lo
# lee. Recalibrado a 5 (300s) el 2026-08-20 (pago de deuda ronda 3, T1
# paso 1/3) por instrucción directa de Fernando -- alinear con el default
# real que ya corre en produccion (jacobs/plan.py::_DEFAULT_TIMEOUT_
# SECONDS=300) en vez de con un placeholder sin evidencia. Sigue sin
# consumir ningun timeout real hoy (enforcer sin cablear, ver
# CONTEXT.md) -- este cambio tampoco altera comportamiento de produccion.
#
# forbidden_paths reutiliza EXACTO el mismo array ya usado por
# code_swarm/implementation -- no una lista nueva paralela.
# allowed_callers=['jacobs']: unico caller real (GAP2 Fase1, gate
# literal de motor=='jax_local' en worker.py, siempre despachado como
# caller='jacobs').
_FILE_CAPABILITY_SEED = [
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

# Modo de cada capability sembrada (tanda A v3, 2026-09-14, decisión de
# Fernando): 'mutating' SOLO file_write, la única que cambia el estado del
# sistema. Las demás producen texto o parches sin aplicarlos. UNA fuente:
# la usan los INSERT de las semillas y _backfill_capability_mode. Sin
# default a propósito, mismo conjunto que el CHECK de la columna (ver
# CREATE_CAPABILITY). tests/test_capability_mode.py es el tripwire de que
# cubre exactamente lo sembrado.
_CAPABILITY_MODE: dict[str, str] = {
    **{fila[0]: "read_only" for fila in _CAPABILITY_SEED},
    "file_read": "read_only",
    "file_write": "mutating",
}

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
            " allowed_callers, forbidden_paths, mode) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (key, risk_level, sandbox_only, gate, max_exec, max_rec, schema,
             effective_fallback_motor, fallback_mode, json.dumps(callers),
             json.dumps(forbidden) if forbidden is not None else None,
             _CAPABILITY_MODE[key]),
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
    # Repara tambien el valor LEGACY sin /v1, no solo NULL/''. La condicion
    # original no podia dispararse en una instalacion nueva porque
    # _seed_providers ya habia insertado un valor no vacio -- ver el
    # comentario en _PROVIDER_SEED. Se conserva la migracion (ademas del seed
    # corregido) porque las DB que ya existen no vuelven a pasar por el INSERT.
    await cur.execute(
        "UPDATE provider SET base_url='http://localhost:11434/v1' "
        "WHERE id='ollama' AND (base_url IS NULL OR base_url = '' "
        "                       OR base_url = 'http://localhost:11434')"
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
    for (key, risk_level, sandbox_only, gate, max_exec, max_rec, schema,
         fallback_motor, fallback_mode, callers, forbidden) in _FILE_CAPABILITY_SEED:
        await cur.execute(
            "INSERT IGNORE INTO capability "
            "(`key`, risk_level, sandbox_only, requires_human_gate, max_execution_minutes, "
            " max_recursion_depth, output_schema, fallback_motor, fallback_mode, "
            " allowed_callers, forbidden_paths, mode) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (key, risk_level, sandbox_only, gate, max_exec, max_rec, schema,
             fallback_motor, fallback_mode, json.dumps(callers), json.dumps(forbidden),
             _CAPABILITY_MODE[key]),
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
    # gpt-5.6-terra (PR-L ronda 1): el binding semilla de thot. Con gpt-5.5,
    # en una base vacía esa fila ya no existe y el motor thot no se sembraba.
    await cur.execute(
        "SELECT id FROM model WHERE provider_id='openai' AND model_id='gpt-5.6-terra'"
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
    # /v1 EN EL SEED, no solo en la migracion correctiva de mas abajo. Hasta
    # 2026-09-01 el seed insertaba sin /v1 y _seed_jax_local_motor lo corregia
    # solo `WHERE base_url IS NULL OR base_url = ''` -- condicion que este
    # mismo INSERT vuelve falsa. Resultado: en la DB de Fernando quedo bien
    # (la fila existia vacia cuando la migracion corrio por primera vez) y en
    # cualquier INSTALACION NUEVA quedaba mal para siempre. Medido el
    # 2026-09-01 contra un mariadb:11.8 vacio: 'http://localhost:11434'.
    ("ollama",   "Ollama (jax_local)", "http://localhost:11434/v1",                          "none",       True),
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
# compatibles (header_bearer) + Gemini (header_goog_api_key: la key en la
# cabecera x-goog-api-key, desde T6-2 del 2026-09-15; antes query_param)
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
    # T6-2 (2026-09-15): antes 'query_param' (key en la URL).
    ("gemini",    "header_goog_api_key", "https://generativelanguage.googleapis.com/v1beta/models"),
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


async def _raise_generate_execution_ceiling(cur) -> None:
    """generate: techo de ejecución 5 -> 15 min (2026-09-12, GO de Fernando).

    Pipeline b8f80733: kimi (motor de razonamiento) generó 8000 tokens en
    ~274 s -- una llamada más el reintento de schema no caben en 5 min, el
    paso venció y abortó el pipeline entero. 15 es el techo que ya tienen
    design/reason/reconcile, las otras capabilities de trabajo largo.

    El seed usa INSERT IGNORE, así que cambiar la tupla solo alcanza a bases
    nuevas; esto corrige las existentes. Guard WHERE =5: corrige el valor
    viejo una vez y no pisa un ajuste manual posterior (mismo criterio que
    _fix_file_write_gate_and_auditor)."""
    await cur.execute(
        "UPDATE capability SET max_execution_minutes=15 "
        "WHERE `key`='generate' AND max_execution_minutes=5"
    )


MIGRACION_MOTOR_TOPE_AL_CATALOGO_V1 = "motor_max_tokens_al_catalogo_v1"
MOTORES_AL_TOPE_DEL_CATALOGO = ("kimi", "ada")


async def _motor_max_tokens_al_catalogo_v1(cur) -> None:
    """D1 de Fernando (spec 2026-09-17 §1 y §7 A): kimi y ada pasan a
    motor.max_tokens=0 -- el tope efectivo es model.max_output_tokens del
    catálogo (worker._limite_del_motor: 0 = sin tope propio). El seed usa
    INSERT IGNORE, así que la tupla nueva sólo alcanza a bases nuevas; esto
    corrige las existentes UNA vez (marcador): un ajuste posterior desde Admin
    no se pisa al arrancar."""
    await cur.execute("SELECT 1 FROM axioma_migracion_de_datos WHERE nombre = %s",
                      (MIGRACION_MOTOR_TOPE_AL_CATALOGO_V1,))
    if await cur.fetchone() is not None:
        return
    marcas = ", ".join(["%s"] * len(MOTORES_AL_TOPE_DEL_CATALOGO))
    await cur.execute(f"UPDATE motor SET max_tokens = 0 WHERE `key` IN ({marcas})", MOTORES_AL_TOPE_DEL_CATALOGO)
    await cur.execute("INSERT INTO axioma_migracion_de_datos (nombre) VALUES (%s)",
                      (MIGRACION_MOTOR_TOPE_AL_CATALOGO_V1,))


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
    # Sesiones que se cortan de verdad (2026-09-12, admin usuarios etapa 2):
    # access y refresh llevan `tv`; subir esta columna invalida TODOS los
    # tokens del usuario en el request siguiente. NOT NULL DEFAULT 0 para las
    # filas existentes: un token viejo (sin `tv`) vale como 0 y nadie queda
    # afuera al desplegar.
    ("jax_users", "token_version", "ALTER TABLE jax_users ADD COLUMN token_version INT NOT NULL DEFAULT 0"),
    # Baja en vez de DELETE (2026-09-12, admin usuarios etapa 5, spec §3.5).
    ("jax_users", "deleted_at", "ALTER TABLE jax_users ADD COLUMN deleted_at DATETIME NULL"),
    ("jax_users", "deleted_by", "ALTER TABLE jax_users ADD COLUMN deleted_by INT NULL"),
    # Cambio obligatorio de contraseña (2026-09-15, fijar contraseña por admin,
    # Ruling U34). NOT NULL DEFAULT FALSE: las filas existentes quedan sin la
    # marca y nadie queda encerrado al desplegar. La prende sólo
    # POST /api/admin/users/{id}/password; la apagan Mi cuenta y /reset-password.
    ("jax_users", "must_change_password",
     "ALTER TABLE jax_users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT FALSE"),
    # Bloque D (D1.1/D1.3) — divergencia real ya presente en
    # api/admin/keys.py (Gemini: cabecera x-goog-api-key desde T6-2 del
    # 2026-09-15 -- valor 'header_goog_api_key', agregado al ENUM en
    # _ENUM_EXTENSIONS --; los otros 4: Authorization: Bearer). Este ALTER es
    # el historico de la columna, no se toca. models_list_url NULL = sin sync automatico de capa (a)
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
    # 2026-08-27, segunda mitad del mismo incidente: arreglado el NOMBRE del
    # parametro, la API de gpt-5.6-terra rechazo el VALOR --
    # HTTP 400 "max_tokens is too large: 131072. This model supports at most
    # 128000 completion tokens, whereas you provided 131072". _call_openai_compat
    # mandaba 131072 fijo (constante _MAX_OUTPUT_TOKENS), que era universal
    # mientras todos los modelos del camino openai-compat lo aceptaran; dejo de
    # serlo. Es la misma clase de hecho que max_tokens_param: una propiedad
    # estable POR MODELO, no una constante del despachador.
    #
    # NO se deriva de context_window (verificado contra la DB en vivo, no
    # supuesto): gpt-5.6-terra tiene context_window=1050000 -- la ventana TOTAL,
    # entrada+salida -- contra un tope de COMPLETION de 128000. Son dos hechos
    # distintos y el segundo no existia en el catalogo hasta esta columna.
    #
    # NULL, sin DEFAULT, es la misma decision deliberada de max_tokens_param: un
    # modelo sin valor FALLA RUIDOSO en el dispatch
    # (api/chat.py::_max_output_tokens_value) en vez de asumir. Un default de
    # 131072 volveria a romper en silencio contra el proximo modelo con tope mas
    # bajo; uno "conservador" (ej. 4096) truncaria respuestas de modelos de
    # razonamiento sin que nadie se entere, que es el bug que la constante
    # explicita existia para prevenir (017ba2f). Nace con lector
    # (_max_output_tokens_value, via facet_resolver.ResolvedFacet.max_output_tokens)
    # y con test (tests/test_model_max_output_tokens.py).
    ("model", "max_output_tokens",
     "ALTER TABLE model ADD COLUMN max_output_tokens INT NULL"),
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
    # SP3 grounding (2026-09-03). shadow_messages: qué vio el modelo y su
    # hash. Tres estados distinguibles a propósito (spec §5.4): NULL = turno
    # anterior a esta migración; 'ERROR' = el snapshot falló al construirse;
    # 64 hex = snapshot real. shadow_claim_verdicts: authority SIEMPRE
    # derivada por el servidor, nunca lo que mandó el modelo (spec §9.1);
    # evidence_pointer tal como se recibió, truncado a 100 (el original va a
    # `detail` si excede).
    ("shadow_messages", "grounding_snapshot",
     "ALTER TABLE shadow_messages ADD COLUMN grounding_snapshot LONGTEXT NULL"),
    ("shadow_messages", "grounding_snapshot_sha256",
     "ALTER TABLE shadow_messages ADD COLUMN grounding_snapshot_sha256 CHAR(64) NULL"),
    ("shadow_claim_verdicts", "authority",
     "ALTER TABLE shadow_claim_verdicts ADD COLUMN authority VARCHAR(12) NULL"),
    ("shadow_claim_verdicts", "evidence_pointer",
     "ALTER TABLE shadow_claim_verdicts ADD COLUMN evidence_pointer VARCHAR(100) NULL"),
    # 2026-09-03: texto CRUDO tal como lo emitió el modelo, acotado por bytes
    # (shadow_validation.py::_RAW_COLUMN_BYTES). Es el ÚNICO lugar donde
    # queda el bloque `analysis` -- donde el modelo explica por qué eligió
    # el puntero que eligió -- que hasta hoy no se persiste en ningún lado.
    # Sin eso no se puede auditar una citación equivocada (POINTER_MISMATCH
    # / FACT_NOT_IN_SNAPSHOT, ver _reclassify_provenance_mismatch mas abajo).
    ("shadow_messages", "contract_raw",
     "ALTER TABLE shadow_messages ADD COLUMN contract_raw LONGTEXT NULL"),
    # 2026-09-03: origen declarado por quien llama (web/probe/test), grano
    # TURNO. Ver el comentario completo junto a CREATE_SHADOW_MESSAGES
    # arriba -- el default 'unattributed' es deliberado (fail-closed: la
    # ausencia de declaración no es evidencia de uso orgánico) y se repite
    # acá porque un ADD COLUMN sin DEFAULT explícito no hereda el default
    # del CREATE TABLE para las filas ya existentes de una base vieja.
    ("shadow_messages", "origin",
     "ALTER TABLE shadow_messages ADD COLUMN origin VARCHAR(20) NOT NULL DEFAULT 'unattributed'"),
    # Tanda A v3 (2026-09-14): en bases que ya tienen `capability` nace NULL
    # A PROPÓSITO (sin CHECK todavía: el CHECK lo agrega
    # _asegurar_forma_de_capability_mode una vez que no quedan NULL). Un ADD
    # COLUMN ... NOT NULL sin default le pondría a las filas existentes un
    # valor inventado -- file_write quedaría mal clasificado en silencio
    # hasta el UPDATE, y el DDL hace commit implícito. _backfill_capability_
    # mode la rellena desde _CAPABILITY_MODE y _asegurar_forma_de_capability_
    # mode la pasa a VARCHAR(16) NOT NULL + CHECK. También convierte la
    # columna ENUM que ya quedó en producción por el incidente del
    # 2026-09-14 (ver spec §0 v3), conservando los valores.
    ("capability", "mode",
     "ALTER TABLE capability ADD COLUMN mode VARCHAR(16) NULL"),
    # Pre-vuelo (spec 2026-09-17 §4.4): tokens de salida que una capability
    # necesita como mínimo. Jacobs compara el tope efectivo del paso contra
    # esto y rechaza con `tope_insuficiente`. 0 = sin mínimo declarado. La
    # semilla MEDIDA la pone _semilla_min_output_tokens_v1.
    ("capability", "min_output_tokens",
     "ALTER TABLE capability ADD COLUMN min_output_tokens INT NOT NULL DEFAULT 0"),
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
    (
        "facet_health_event", "outcome", "config_error",
        "ALTER TABLE facet_health_event MODIFY COLUMN outcome "
        "ENUM('ok','provider_error','gate_denied','gate_unreachable',"
        "'unbound','unsupported_transport','probe_error','config_error') NOT NULL",
    ),
    # T6-2 (2026-09-15): Gemini manda la key en la cabecera x-goog-api-key.
    # 'query_param' se conserva en el ENUM para que el ALTER no falle sobre
    # filas viejas; _migrar_gemini_a_cabecera las mueve y model_catalog
    # rechaza el valor viejo (fail-closed).
    (
        "provider", "api_key_transport", "header_goog_api_key",
        "ALTER TABLE provider MODIFY COLUMN api_key_transport "
        "ENUM('header_bearer','query_param','header_goog_api_key') NOT NULL DEFAULT 'header_bearer'",
    ),
    # Pre-vuelo (spec 2026-09-17 §4.5): la sonda de Jacobs registra su
    # resultado con source='preflight', así el próximo pre-vuelo dentro de la
    # ventana de salud no vuelve a sondear. Lista COMPLETA de valores.
    (
        "facet_health_event", "source", "preflight",
        "ALTER TABLE facet_health_event MODIFY COLUMN source "
        "ENUM('chat','canary_periodic','canary_rebind','preflight') NOT NULL",
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
    # jax_users.email era VARCHAR(100), pero se valida hasta 254 (RFC 5321) y la
    # baja lo renombra a <original>#baja-<id>-<yyyymmdd> para liberar la
    # dirección: hasta 254 + 25 = 279 (ver email_de_baja en api/admin/users.py).
    # 320 deja margen. MODIFY conserva el UNIQUE (índice de 1280 bytes en
    # utf8mb4, bajo el límite de 3072 de InnoDB).
    (
        "jax_users", "email", 320,
        "ALTER TABLE jax_users MODIFY COLUMN email VARCHAR(320) NOT NULL",
    ),
]


# (tabla, índice, DDL) -- agrega un índice a una tabla EXISTENTE si falta,
# con un execute SIN cota de espera (lock_wait_timeout por defecto, 86400 s).
# Un índice nuevo sobre una tabla caliente NO va acá: va por
# _crear_indice_acotado (ver _indice_de_uso_por_periodo y
# _indice_de_cuentas_bloqueadas). Las tablas nuevas declaran sus índices en
# su CREATE TABLE.
# idx_jax_users_role_status: el conteo de superadmins activos de la
# invariante (api/admin/users.py::otros_superadmins_activos) filtra por
# role y status, con FOR UPDATE (2026-09-12, admin usuarios etapa 3).
# EXPLAIN en tests/test_user_audit.py.
_INDEXES = [
    ("jax_users", "idx_jax_users_role_status",
     "ALTER TABLE jax_users ADD INDEX idx_jax_users_role_status (role, status)"),
]


# Fix wave final, item 8 (2026-09-15): GET /api/admin/usage filtra por
# created_at y agrupa por (facet, model, request_type) o (facet, dia). Sin
# indice era un scan completo de axioma_usage en cada pedido (gate U29: p95
# ~2,7 s con 102k filas). Cubriente: el rango de fechas se lee del indice con
# todas las columnas que suman las dos consultas, sin tocar la fila base.
# axioma_usage es de la plataforma (CREATE_AXIOMA_USAGE, arriba); jax solo
# inserta. ALGORITHM=INPLACE, LOCK=NONE explicitos: si MariaDB no puede
# crearlo en linea, FALLA en vez de caer a COPY, que bloquea los INSERT de
# record_usage (y de Jacobs/LAS MANOS) mientras copia.
DDL_INDICE_USO_POR_PERIODO = (
    "ALTER TABLE axioma_usage ADD INDEX idx_axioma_usage_periodo "
    "(created_at, facet, model, request_type, tokens_in, tokens_out, cost_usd), "
    "ALGORITHM=INPLACE, LOCK=NONE"
)

# Espera maxima por el metadata lock del DDL acotado: el default de MariaDB
# (lock_wait_timeout) es 86400 s, y una transaccion larga sobre la tabla
# dejaria el arranque colgado un dia. Mientras el DDL espera su MDL exclusivo,
# las lecturas y escrituras NUEVAS de la tabla se encolan detras: por eso
# 30 s, la misma cota que jax/jacobs/store.py.
_LOCK_WAIT_DDL_SEGUNDOS = 30
_ER_LOCK_WAIT_TIMEOUT = 1205


async def _crear_indice_acotado(cur, tabla: str, indice: str, ddl: str) -> bool:
    """Corre `ddl` con lock_wait_timeout de 30 s en ESTA sesion y restaura
    el valor previo pase lo que pase. True si lo creo.

    Generico pese al nombre: `indice` es solo el objeto que se nombra en el
    log, y desde 2026-09-15 tambien lo usa la columna spool_id
    (_respaldo_de_uso). La politica de espera es la misma para cualquier DDL
    en linea sobre una tabla caliente.

    Politica de jax/jacobs/store.py::_crear_indice_acotado: si la espera vence
    (1205), ERROR en el log con el indice y el motivo, y el arranque SIGUE --
    el proximo arranque lo reintenta, porque _index_exists ve que falta. El
    indice es de rendimiento, no un contrato: sin el, las consultas de uso
    siguen correctas (scan). Cualquier OTRO error (INPLACE o LOCK=NONE no
    soportados, sintaxis) SUBE: no es una espera, es un DDL que no puede correr
    como se declaro."""
    await cur.execute("SELECT @@SESSION.lock_wait_timeout")
    (previo,) = await cur.fetchone()
    await cur.execute("SET SESSION lock_wait_timeout=%s", (_LOCK_WAIT_DDL_SEGUNDOS,))
    try:
        await cur.execute(ddl)
        return True
    except aiomysql.OperationalError as e:  # fail-soft: el indice solo acelera; las consultas de uso siguen correctas como scan; 1205 se reintenta en el proximo arranque y todo otro error sube
        if not (e.args and e.args[0] == _ER_LOCK_WAIT_TIMEOUT):
            raise
        logger.error(
            "run_migrations: no se creo %s en %s -- otra transaccion tiene la tabla y "
            "vencio la espera de %d s (%s). El arranque sigue SIN el indice (las "
            "consultas de uso hacen scan); se reintenta en el proximo arranque.",
            indice, tabla, _LOCK_WAIT_DDL_SEGUNDOS, e,
        )
        return False
    finally:
        await cur.execute("SET SESSION lock_wait_timeout=%s", (int(previo),))


async def _indice_de_uso_por_periodo(cur) -> None:
    """Idempotente: solo crea idx_axioma_usage_periodo si falta."""
    if not await _index_exists(cur, "axioma_usage", "idx_axioma_usage_periodo"):
        await _crear_indice_acotado(
            cur, "axioma_usage", "idx_axioma_usage_periodo", DDL_INDICE_USO_POR_PERIODO)


# Task 15 R12c (2026-09-16): el conteo de cuentas bloqueadas del tablero
# (api/admin/dashboard.py::SQL_CUENTAS_BLOQUEADAS) era `ALL` sobre jax_users.
# jax_users la lee CADA request autenticado (auth/middleware.py,
# SQL_ESTADO_DE_SESION): un ALTER esperando su metadata lock exclusivo encola
# detrás todas las lecturas nuevas. Por eso va por _crear_indice_acotado (30 s
# y el arranque sigue sin el índice si vence), igual que
# idx_axioma_usage_periodo; INPLACE/LOCK=NONE explícitos para que falle en vez
# de caer a COPY. EXPLAIN en tests/test_tablero.py.
DDL_INDICE_CUENTAS_BLOQUEADAS = (
    "ALTER TABLE jax_users ADD INDEX idx_jax_users_locked_until (locked_until), "
    "ALGORITHM=INPLACE, LOCK=NONE"
)


async def _indice_de_cuentas_bloqueadas(cur) -> None:
    """Idempotente: solo crea idx_jax_users_locked_until si falta."""
    if not await _index_exists(cur, "jax_users", "idx_jax_users_locked_until"):
        await _crear_indice_acotado(
            cur, "jax_users", "idx_jax_users_locked_until", DDL_INDICE_CUENTAS_BLOQUEADAS)


# Cola durable para el registro de uso (2026-09-15, Task 2 del plan
# 2026-09-15-cola-durable-uso). `spool_id` es lo que hace IDEMPOTENTE al
# reintento: el drenaje inserta la fila del respaldo con su spool_id y borra el
# archivo DESPUES; si el proceso se muere entre las dos cosas, el ciclo
# siguiente reinserta la misma fila y el UNIQUE (con INSERT IGNORE) evita
# cobrarla dos veces.
#
# NULL y sin default: las filas del camino feliz no lo escriben, y un indice
# UNIQUE admite varios NULL en MariaDB -- si no, la segunda fila de chat
# fallaria. CHAR(36) es exactamente un UUID canonico con guiones (el nombre del
# archivo del respaldo, uso/cola.py).
#
# ALGORITHM=INPLACE, LOCK=NONE explicitos en los dos DDL, por lo mismo que el
# indice de periodo: si MariaDB no puede hacerlo en linea, que FALLE en vez de
# caer a COPY y bloquear los INSERT de record_usage (y los de Jacobs y LAS
# MANOS) mientras copia la tabla.
COLUMNA_SPOOL_ID = "spool_id"
INDICE_SPOOL_ID = "uniq_axioma_usage_spool_id"
DDL_COLUMNA_SPOOL_ID = (
    "ALTER TABLE axioma_usage ADD COLUMN spool_id CHAR(36) NULL, "
    "ALGORITHM=INPLACE, LOCK=NONE"
)
DDL_INDICE_SPOOL_ID = (
    f"ALTER TABLE axioma_usage ADD UNIQUE INDEX {INDICE_SPOOL_ID} ({COLUMNA_SPOOL_ID}), "
    "ALGORITHM=INPLACE, LOCK=NONE"
)


async def _respaldo_de_uso(cur) -> None:
    """Idempotente: agrega spool_id y su UNIQUE a axioma_usage si faltan.

    Los dos DDL van por `_crear_indice_acotado` (que es generico: corre
    CUALQUIER DDL con la espera acotada de 30 s y restaura el valor previo).
    Si la espera de la COLUMNA vence (1205), el indice NO se intenta: un UNIQUE
    sobre una columna que no existe es un error duro que tiraria el arranque,
    y no una espera reintentable. Los dos quedan para el proximo arranque, que
    ve que faltan.
    """
    if not await _column_exists(cur, "axioma_usage", COLUMNA_SPOOL_ID):
        if not await _crear_indice_acotado(
                cur, "axioma_usage", COLUMNA_SPOOL_ID, DDL_COLUMNA_SPOOL_ID):
            return
    if not await _index_exists(cur, "axioma_usage", INDICE_SPOOL_ID):
        await _crear_indice_acotado(
            cur, "axioma_usage", INDICE_SPOOL_ID, DDL_INDICE_SPOOL_ID)


async def _index_exists(cur, table_name: str, index_name: str) -> bool:
    await cur.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND INDEX_NAME = %s
        """,
        (table_name, index_name),
    )
    row = await cur.fetchone()
    return bool(row and row[0] > 0)


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
#   - deepseek-flash / deepseek-v4-pro (provider deepseek) -> 'max_tokens'.
#     Agregados 2026-09-14 (PR-J): DeepSeek renombro deepseek-v4-flash ->
#     deepseek-flash, se aprobo la propuesta de drift (#11) y jekyll quedo
#     apuntando a una fila sin este dato -> caida desde 2026-09-12 19:40 CST.
#     Fuente: doc oficial https://api-docs.deepseek.com (leida 2026-09-14): el
#     parametro es `max_tokens`, NO existe `max_completion_tokens` en esa API;
#     modelos vigentes deepseek-flash y deepseek-v4-pro (deepseek-v4-flash es
#     el nombre legado retirado; su fila se deja, describe un modelo real).
_MODEL_MAX_TOKENS_PARAM_SEED = [
    ("openai",   "gpt-5.6-terra",      "max_completion_tokens"),
    ("deepseek", "deepseek-v4-flash",  "max_tokens"),
    ("deepseek", "deepseek-flash",     "max_tokens"),
    ("deepseek", "deepseek-v4-pro",    "max_tokens"),
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


# (provider_id, model_id, max_output_tokens) — mismos 4 modelos del camino
# openai-compat que _MODEL_MAX_TOKENS_PARAM_SEED, con el tope de tokens de
# SALIDA que cada API acepta:
#   - gpt-5.6-terra (thot, provider openai) -> 128000. Lo dijo la propia API en
#     el HTTP 400 del incidente: "max_tokens is too large: 131072. This model
#     supports at most 128000 completion tokens, whereas you provided 131072".
#     No es una estimacion nuestra ni se derivo de context_window (que para este
#     modelo es 1050000, la ventana TOTAL — otro hecho).
#   - deepseek-v4-flash (jekyll), glm-5.3 (ada), kimi-k3 -> 131072.
# Los ultimos tres van con 131072 A PROPOSITO: es exactamente el valor que el
# codigo mandaba fijo (_MAX_OUTPUT_TOKENS) y con el que funcionan hoy en
# produccion, asi que sembrarlo es cambio de comportamiento CERO para jekyll y
# ada. Sembrar solo el de thot los tumbaria: NULL -> fallo ruidoso.
#   - deepseek-flash / deepseek-v4-pro -> 393216. Agregados 2026-09-14 (PR-J,
#     ver _MODEL_MAX_TOKENS_PARAM_SEED). Fuente: doc oficial
#     https://api-docs.deepseek.com (leida 2026-09-14): max_tokens admite de 1
#     a 393216 (384K). El valor es DECISION de Fernando (2026-09-14): el maximo
#     documentado, no una estimacion ni el 131072 heredado.
_MODEL_MAX_OUTPUT_TOKENS_SEED = [
    ("openai",   "gpt-5.6-terra",      128000),
    ("deepseek", "deepseek-v4-flash",  131072),
    ("deepseek", "deepseek-flash",     393216),
    ("deepseek", "deepseek-v4-pro",    393216),
    ("zhipu",    "glm-5.3",            131072),
    ("moonshot", "kimi-k3",            131072),
    # PR-L ronda 3 (2026-09-14, decisión de Fernando ~20:50): el modelo del
    # binding de jax_local en producción (model id 1556, hoy NULL/NULL). Con
    # PR-K (jax) todo camino Ollama exige max_output_tokens, así que sin esta
    # fila jax_local dejaría de despachar. 262144 = su contexto, leído con
    # `ollama show` (qwen35moe 36.0B, context length 262144); la doc oficial
    # de Ollama dice num_predict default -1 = generación sin tope, o sea que
    # hoy no tiene ninguno y el contexto es el techo real. SOLO esta lista: el
    # contrato de transporte de ollama no lee max_tokens_param (ollama no está
    # en contrato_dispatch.TRANSPORTS_CON_CONTRATO_DE_DISPATCH), así que no se
    # siembra un nombre de parámetro que nadie usa. WHERE IS NULL como el resto.
    ("ollama",   "qwen3.6:35b-a3b-q4_K_M", 262144),
    # Pre-vuelo (spec 2026-09-17 §7 F): medido 2026-09-17 con
    # GET https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash
    # (x-goog-api-key, metadata sin costo) -> outputTokenLimit=65536.
    ("gemini",   "gemini-2.5-flash",   65536),
    # Pre-vuelo (spec 2026-09-17 §7 F): medido 2026-09-17 con
    # GET https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash
    # (x-goog-api-key, metadata sin costo) -> outputTokenLimit=65536. Es el
    # binding primario de hipatia en producción (tabla B, max_output_tokens NULL).
    ("gemini",   "gemini-3.8-flash",   65536),
]


async def _seed_model_max_output_tokens(cur) -> None:
    """Siembra model.max_output_tokens para los modelos cuyo tope esta
    verificado (ver _MODEL_MAX_OUTPUT_TOKENS_SEED).

    Deliberadamente NO siembra el resto del catalogo, ni lo deriva de
    context_window: la ventana total y el tope de completion son hechos
    distintos (gpt-5.6-terra: 1050000 vs 128000), y derivar uno del otro es
    exactamente la suposicion que esta columna existe para eliminar. Un modelo
    fuera de esta lista queda NULL y hace fallar el dispatch con un mensaje que
    dice que fila sembrar (api/chat.py::_max_output_tokens_value) — ruidoso por
    diseno.

    Guard WHERE max_output_tokens IS NULL: idempotente y no pisa un valor puesto
    a mano si un operador ya sembro algo distinto (mismo patron que
    _seed_model_max_tokens_param / _seed_http_facet_allowed_callers)."""
    for provider_id, model_id, limit in _MODEL_MAX_OUTPUT_TOKENS_SEED:
        await cur.execute(
            "UPDATE model SET max_output_tokens = %s "
            "WHERE provider_id = %s AND model_id = %s AND max_output_tokens IS NULL",
            (limit, provider_id, model_id),
        )


MIGRACION_MIN_OUTPUT_TOKENS_V1 = "capability_min_output_tokens_v1"
# Pre-vuelo (spec 2026-09-17 §4.4): tokens de SALIDA que cada capability
# necesitó como máximo en corridas COMPLETADAS, redondeado hacia arriba a
# múltiplo de 1024. MEDIDO 2026-09-17 contra jax_memory (producción), sesión
# READ ONLY: pasos HTTP de Jacobs = axioma_usage.tokens_out unido a
# jacobs_steps completados por faceta y ventana [started_at, finished_at+5 s]
# (filas ambiguas entre capabilities excluidas); pasos de Motor Registry =
# _usage.completion_tokens de las_manos/logs/motor_jobs.jsonl (último registro
# por job_id, status completed). Script y salida: ver el commit que agrega
# esta constante. Una capability sin corridas medibles no está acá y queda en
# 0 (sin mínimo), declarado en el mismo commit.
MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17: dict[str, int] = {
    "analysis": 16384,  # max=15618, corridas=4
    "critique": 13312,  # max=12426, corridas=7
    "design": 14336,  # max=14293, corridas=6
    "file_write": 2048,  # max=1301, corridas=7
    "generate": 14336,  # max=14006, corridas=6
    "reconcile": 21504,  # max=20664, corridas=5
    "research": 7168,  # max=6790, corridas=9
    "validate_consistency": 3072,  # max=3018, corridas=5
}


async def _semilla_min_output_tokens_v1(cur) -> None:
    """UNA vez (marcador): después, lo que el admin cambie no se pisa al
    arrancar. Sin marcador y a medias, la próxima corrida la completa: cada
    sentencia fija el mismo valor."""
    await cur.execute("SELECT 1 FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_MIN_OUTPUT_TOKENS_V1,))
    if await cur.fetchone() is not None:
        return
    for clave, minimo in MIN_OUTPUT_TOKENS_MEDIDOS_2026_09_17.items():
        await cur.execute("UPDATE capability SET min_output_tokens = %s WHERE `key` = %s", (minimo, clave))
    await cur.execute("INSERT INTO axioma_migracion_de_datos (nombre) VALUES (%s)", (MIGRACION_MIN_OUTPUT_TOKENS_V1,))


async def _migrate_kimi_chat_transport(cur) -> None:
    """2026-09-11: kimi pasa de transport='motor_registry' a
    'http_openai_compat' en `facet`, igual que ada.

    'motor_registry' era una etiqueta sin lector. facet.transport lo leen
    el chat (api/chat.py::_invoke_facet_dispatch) y Jacobs
    (jacobs/executor.py, repo jax), y ninguno despachaba ese valor: Jacobs
    manda a kimi por _MOTOR_FACETS ANTES de mirar facet.transport, y el
    worker de Motor Registry usa `motor.transport`, que para kimi ya es
    'http_openai_compat'. En el chat caia en `unsupported_transport`:
    medido, cada barrido de la sonda desde 2026-08-20 y cero turnos
    servidos desde 2026-08-18, con la API de Moonshot sana.

    Guard WHERE transport='motor_registry': corrige SOLO el valor viejo
    conocido; un transporte puesto a mano despues no se revierte. Tiene
    que correr antes de _seed_http_facet_allowed_callers, que le da a
    kimi el acceso al gate authorize-facet que el transporte nuevo exige."""
    await cur.execute(
        "UPDATE facet SET transport = 'http_openai_compat' "
        "WHERE `key` = 'kimi' AND transport = 'motor_registry'"
    )


async def _seed_http_facet_allowed_callers(cur) -> None:
    """Gobernanza de _HTTP_FACETS (docs/superpowers/specs/2026-08-27-
    http-facets-motor-policy-governance-design.md): hipatia/jekyll/thot/
    ada quedan con allowed_callers=["jacobs","jax_platform_chat"] --
    mismo acceso que ya existia informalmente (ninguno de los dos estaba
    bloqueado antes de esta ronda), ahora explicito. kimi se suma el
    2026-09-11, cuando pasa a despacharse por http_openai_compat en el chat
    (ver _migrate_kimi_chat_transport): sin esta fila el gate la denegaria
    fail-closed. jax_local/hyde quedan NULL a proposito -- no pasan por el
    gate (transportes ollama/subprocess), fail-closed por diseno (ver
    facet_policy.py::check_facet_admission en el repo jax).

    Guard WHERE allowed_callers IS NULL: no pisa un valor manual futuro
    si alguien ya lo configuro distinto."""
    await cur.execute(
        "UPDATE facet SET allowed_callers = %s "
        "WHERE `key` IN ('hipatia','jekyll','thot','ada','kimi') AND allowed_callers IS NULL",
        (json.dumps(["jacobs", "jax_platform_chat"]),),
    )


async def _reclassify_provenance_mismatch(cur) -> None:
    """2026-09-03: PROVENANCE_MISMATCH se partió en dos estados en el repo
    `jax` (policy/governance/grounding.py + validator.py, commit 4617df6):
    POINTER_MISMATCH (el hecho afirmado SÍ está en el snapshot, el modelo
    citó otro puntero) y FACT_NOT_IN_SNAPSHOT (ninguna entrada del snapshot
    respalda el hecho afirmado). Esta función reclasifica las filas viejas
    de `shadow_claim_verdicts` que quedaron con el status ya retirado.

    RECLASIFICA, NO BORRA: cada fila conserva su predicate/detail/args/
    authority/evidence_pointer originales, solo cambia `status`.

    La condición es la MISMA condición mecánica que aplica el código nuevo
    (shadow_validation.py vía grounding.accredit()/mismatch(), repo jax):
    con los args del claim (cv.args, ya como se guardaron -- ver
    _insert_claim_verdict, son los args crudos del claim, valores siempre
    string en los predicados con resolver hoy -- ASUMIDO y medido, no
    garantizado por el tipo de la columna: `cv.args` se guarda con
    json.dumps() sin pasar por normalize_args(), asi que si algun
    predicado futuro aceptara args no-string, esta migracion los
    clasificaria como FACT_NOT_IN_SNAPSHOT donde el runtime, que si
    normaliza, diria POINTER_MISMATCH) ¿existe alguna entrada del
    snapshot de ESE turno con el mismo predicado y los mismos args?
    JSON_CONTAINS(array, valor) compara SEMÁNTICAMENTE -- el orden de las
    claves del objeto no importa, a diferencia de una comparación de string
    -- así que no hace falta normalizar el objeto antes de comparar.
    JSON_EXTRACT(sm.grounding_snapshot, '$.capabilities') saca el array de
    entradas del snapshot. Desde la tanda A v2 (2026-09-14) build_snapshot()
    produce también la sección "catalog_capabilities"; esta reclasificación
    mira solo '$.capabilities' A PROPÓSITO: aplica únicamente a filas
    históricas con PROVENANCE_MISMATCH (status que ya no se produce desde el
    2026-09-03), cuyos snapshots tenían una sola sección, "capabilities";
    si existe -> POINTER_MISMATCH (citó mal algo verdadero), si no ->
    FACT_NOT_IN_SNAPSHOT (inventó el hecho, mas grave).

    IDEMPOTENTE: el WHERE filtra por status='PROVENANCE_MISMATCH', que esta
    UPDATE elimina en la primera corrida -- una segunda corrida no encuentra
    filas que tocar y no cambia nada (ver
    test_reclassify_provenance_mismatch.py::test_second_run_is_a_no_op).

    Filas con grounding_snapshot NULL (turno anterior a SP3) o
    grounding_snapshot_sha256='ERROR' (el snapshot falló al construirse en
    su turno) se dejan INTACTAS a propósito: no hay snapshot contra el cual
    recomputar la condición mecánica para esas filas -- no es que se
    ignoren por descuido, es que no hay con qué. Medido contra la DB real
    (jax_memory) el 2026-09-03: hoy no existe ninguna fila
    PROVENANCE_MISMATCH con grounding_snapshot NULL o 'ERROR' -- la
    columna se agregó junto con SP3 (grounding), así que toda fila
    PROVENANCE_MISMATCH anterior a SP3 tiene grounding_snapshot NULL por
    definición y ya no puede existir en el momento en que este fix se
    escribió (SP3 se desplegó y las verdicts PROVENANCE_MISMATCH post-SP3
    ya traen snapshot). Se deja el guard igual, explícito, para no asumir
    ese hecho como invariante permanente."""
    await cur.execute(
        "UPDATE shadow_claim_verdicts cv "
        "JOIN shadow_messages sm USING (shadow_message_id) "
        "SET cv.status = CASE "
        "WHEN JSON_CONTAINS(JSON_EXTRACT(sm.grounding_snapshot, '$.capabilities'), cv.args) "
        "THEN 'POINTER_MISMATCH' ELSE 'FACT_NOT_IN_SNAPSHOT' END "
        "WHERE cv.status = 'PROVENANCE_MISMATCH' "
        "AND sm.grounding_snapshot IS NOT NULL "
        "AND sm.grounding_snapshot_sha256 <> 'ERROR'"
    )


async def _backfill_capability_mode(cur) -> None:
    """Rellena `mode` en las filas que ya existían sin columna. Solo toca
    NULL: nunca pisa un modo ya declarado."""
    for key, mode in _CAPABILITY_MODE.items():
        await cur.execute(
            "UPDATE capability SET mode=%s WHERE `key`=%s AND mode IS NULL", (mode, key)
        )


async def _asegurar_forma_de_capability_mode(cur) -> None:
    """Deja `capability.mode` en su forma final: VARCHAR(16) NOT NULL +
    CHECK, sin default (tanda A v3, spec §0/§3.1). Idempotente, en orden:

    (a) si queda una capability sin modo (una fila que ninguna migración
        sembró), la migración FALLA con su nombre: no se le inventa un modo
        (P10).
    (b) si el tipo de columna no es exactamente `varchar(16)` o todavía
        admite NULL, `MODIFY COLUMN mode VARCHAR(16) NOT NULL` -- esto
        también convierte el `ENUM('read_only','mutating') NOT NULL` que ya
        quedó en producción por el incidente del 2026-09-14 (ver spec §0
        v3): un `MODIFY` de ENUM a VARCHAR conserva los valores (medido).
    (c) si no existe un CHECK llamado `chk_capability_mode` para
        `capability` (se consulta `information_schema.CHECK_CONSTRAINTS`
        antes de agregarlo: repetir el `ADD CONSTRAINT` da el error 1826),
        se agrega -- pero antes se revisa a mano si queda una fila con un
        `mode` fuera de {'read_only','mutating'} (una columna VARCHAR sin
        CHECK todavía no rechaza nada) y, si la hay, la migración FALLA con
        su nombre (mismo criterio que el paso (a)): sin esa revisión, el
        propio `ADD CONSTRAINT` fallaría con el error 4025, que MariaDB no
        acompaña con la fila responsable -- ruidoso pero anónimo.

    Nombre anterior: `_enforce_capability_mode_not_null` (v2, solo ENUM);
    renombrada al pasar a la forma completa de v3."""
    await cur.execute("SELECT `key` FROM capability WHERE mode IS NULL ORDER BY `key`")
    sin_modo = [fila[0] for fila in await cur.fetchall()]
    if sin_modo:
        raise RuntimeError(
            f"capability sin mode declarado: {sin_modo}. Ninguna migración las sembró: "
            "declarar su modo en _CAPABILITY_MODE (db/migrations.py) o borrarlas. "
            "Sin default a propósito, ver spec jax 2026-09-14-gobernanza-catalogo-db-design.md §3.1."
        )

    await cur.execute(
        "SELECT COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'capability' AND COLUMN_NAME = 'mode'"
    )
    fila = await cur.fetchone()
    if fila is not None:
        column_type, is_nullable = fila
        if column_type != "varchar(16)" or is_nullable == "YES":
            await cur.execute(
                "ALTER TABLE capability MODIFY COLUMN mode VARCHAR(16) NOT NULL"
            )

    await cur.execute(
        "SELECT 1 FROM information_schema.CHECK_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'capability' "
        "AND CONSTRAINT_NAME = 'chk_capability_mode'"
    )
    if await cur.fetchone() is None:
        await cur.execute(
            "SELECT `key` FROM capability WHERE mode NOT IN ('read_only','mutating') "
            "ORDER BY `key`"
        )
        modo_invalido = [fila[0] for fila in await cur.fetchall()]
        if modo_invalido:
            raise RuntimeError(
                f"capability con mode invalido (ni 'read_only' ni 'mutating'): "
                f"{modo_invalido}. Corregir el valor a mano (UPDATE capability SET "
                "mode=... WHERE `key`=...) antes de reintentar la migración -- sin "
                "esta revisión, el ADD CONSTRAINT de abajo fallaría con el error "
                "4025 sin decir cuál fila."
            )
        await cur.execute(
            "ALTER TABLE capability ADD CONSTRAINT chk_capability_mode "
            "CHECK (mode IN ('read_only','mutating'))"
        )


async def _auditoria_de_catalogo_sin_fk_duras(cur, tabla: str = "model_catalog_audit") -> None:
    """Lleva la auditoría del catálogo a su forma final (PR-L rondas 1-2) en
    una base donde ya existe con una forma anterior: FK duras a model, a
    model_binding_proposal o a jax_users, sin provider_id/model_id/
    performed_by_email. Ver el comentario de CREATE_MODEL_CATALOG_AUDIT.
    Idempotente: cada paso mira information_schema antes de actuar; en una
    base nueva no hace nada salvo los UPDATE (que no encuentran filas).

    `tabla`: el test de conversión la corre sobre una tabla temporal con otro
    nombre, sin tocar la auditoría real de jax_memory_test (ronda 2). Solo
    acepta nombres de identificador simples: va interpolada en el DDL."""
    if not tabla.replace("_", "").isalnum():
        raise ValueError(f"nombre de tabla inválido: {tabla!r}")
    for columna, ddl in (
        ("provider_id", f"ALTER TABLE {tabla} ADD COLUMN provider_id VARCHAR(50) NULL AFTER model_ref"),
        ("model_id", f"ALTER TABLE {tabla} ADD COLUMN model_id VARCHAR(100) NULL AFTER provider_id"),
        ("performed_by_email",
         f"ALTER TABLE {tabla} ADD COLUMN performed_by_email VARCHAR(255) NULL AFTER performed_by"),
    ):
        if not await _column_exists(cur, tabla, columna):
            await cur.execute(ddl)

    # Los nombres de las FK los generó el servidor (en MariaDB, `1`, `2`...):
    # se leen, no se suponen.
    await cur.execute(
        "SELECT CONSTRAINT_NAME FROM information_schema.REFERENTIAL_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = %s "
        "AND REFERENCED_TABLE_NAME IN ('model', 'model_binding_proposal', 'jax_users')",
        (tabla,),
    )
    for (nombre,) in await cur.fetchall():
        await cur.execute(f"ALTER TABLE {tabla} DROP FOREIGN KEY `{nombre}`")

    await cur.execute(
        "SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = %s AND INDEX_NAME = 'idx_facet_rechazo' LIMIT 1",
        (tabla,),
    )
    if await cur.fetchone() is None:
        await cur.execute(f"ALTER TABLE {tabla} ADD INDEX idx_facet_rechazo (action, facet_key, id)")

    # Filas escritas con una forma vieja: se completan mientras la fila de
    # origen exista (con la forma vieja las FK garantizaban que existe).
    await cur.execute(
        f"UPDATE {tabla} a JOIN model m ON m.id = a.model_ref "
        "SET a.provider_id = m.provider_id, a.model_id = m.model_id "
        "WHERE a.model_id IS NULL"
    )
    await cur.execute(
        f"UPDATE {tabla} a JOIN jax_users u ON u.user_id = a.performed_by "
        "SET a.performed_by_email = u.email "
        "WHERE a.performed_by_email IS NULL"
    )


async def _migrar_gemini_a_cabecera(cur) -> None:
    """T6-2 (2026-09-15): la key de Gemini viajaba en `?key=` y terminaba en
    textos de error y logs. Toda fila con el transporte viejo pasa a la
    cabecera. Idempotente (sin filas viejas, no toca nada)."""
    await cur.execute(
        "UPDATE provider SET api_key_transport='header_goog_api_key' "
        "WHERE api_key_transport='query_param'"
    )


MIGRACION_AJUSTES_V1 = "ajustes_que_mandan_v1"
# Lo que el CÓDIGO hacía cumplir en master 26c9cd5 (2026-09-16), no lo que
# mostraba DEFAULT_CONFIG (60 / 1 / 7, que nadie leía):
VALORES_QUE_RIGEN_2026_09_16 = {
    ajustes.SESION: "10080",   # auth/jwt.py: REFRESH_EXPIRE_SECONDS = 7 * 24 * 3600
    ajustes.MAX_PIPELINES: "3",  # jax_engine/resource_manager.py: reemplazó la constante fija por este ajuste (frente C)
    ajustes.RETENCION: "30",   # jax_engine/owner_cleanup.py: COMMAND_OWNER_MAX_AGE_SECONDS
    ajustes.IDIOMA: "es",      # frontend/src/i18n/index.jsx: jax_lang || 'es'
}
# El nombre que la UI mostraba (i18n brandName). La fila, si existe, se conserva:
# el deploy verifica antes que diga esto (plan frente C, Task 16).
NOMBRE_QUE_SE_MOSTRABA_2026_09_16 = "Axioma"
# A-17 (decisión de Fernando, 2026-09-16): ws_notifications sale de
# DEFAULT_CONFIG (nadie lo leía) y su fila de producción la borra esta
# migración, una sola vez.
CLAVE_RETIRADA_WS_NOTIFICATIONS = "ws_notifications"


async def _ajustes_que_mandan_v1(cur) -> None:
    """Frente C (2026-09-16): los ajustes de admin pasan a mandar con el valor
    que ya regía. UNA vez (marcador en axioma_migracion_de_datos): después,
    lo que el admin guarde no se pisa al arrancar. Sin marcador y a medias
    (caída entre sentencias), la próxima corrida la completa: cada sentencia
    fija el mismo valor. De paso (A-17), borra la fila retirada
    ws_notifications -- también una sola vez, bajo el mismo marcador."""
    await cur.execute("SELECT 1 FROM axioma_migracion_de_datos WHERE nombre = %s", (MIGRACION_AJUSTES_V1,))
    if await cur.fetchone() is not None:
        return
    for clave, valor in VALORES_QUE_RIGEN_2026_09_16.items():
        await cur.execute(
            "INSERT INTO axioma_config (config_key, config_value) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE config_value = VALUES(config_value)",
            (clave, valor),
        )
    await cur.execute(
        "INSERT IGNORE INTO axioma_config (config_key, config_value) VALUES (%s, %s)",
        (ajustes.NOMBRE, NOMBRE_QUE_SE_MOSTRABA_2026_09_16),
    )
    await cur.execute("DELETE FROM axioma_config WHERE config_key = %s", (CLAVE_RETIRADA_WS_NOTIFICATIONS,))
    await cur.execute("INSERT INTO axioma_migracion_de_datos (nombre) VALUES (%s)", (MIGRACION_AJUSTES_V1,))


MIGRACION_AJUSTE_CONFIRMAR_USD_V1 = "ajuste_pipeline_confirmar_usd_v1"
# Valor inicial decidido en el spec 2026-09-17 §6.1 (Fernando, GO autónomo).
VALOR_INICIAL_CONFIRMAR_USD = "0.50"


async def _ajuste_confirmar_costo_v1(cur) -> None:
    """Siembra el umbral de confirmación de costo UNA vez (marcador). INSERT
    IGNORE: una fila que ya exista (puesta a mano) se conserva."""
    await cur.execute("SELECT 1 FROM axioma_migracion_de_datos WHERE nombre = %s",
                      (MIGRACION_AJUSTE_CONFIRMAR_USD_V1,))
    if await cur.fetchone() is not None:
        return
    await cur.execute(
        "INSERT IGNORE INTO axioma_config (config_key, config_value) VALUES (%s, %s)",
        (ajustes.CONFIRMAR_USD, VALOR_INICIAL_CONFIRMAR_USD),
    )
    await cur.execute("INSERT INTO axioma_migracion_de_datos (nombre) VALUES (%s)",
                      (MIGRACION_AJUSTE_CONFIRMAR_USD_V1,))


MIGRACION_EJECUTOR_REGLAS_V1 = "ejecutor_reglas_v1"
MIGRACION_EJECUTOR_INVENTARIO_V1 = "ejecutor_inventario_v1"
_SEMILLA_EJECUTOR_REGLAS = Path(__file__).with_name("semilla_ejecutor_reglas.json")
_ROLES_EJECUTOR = ("hypervisor", "desarrollo", "produccion", "clientes", "respaldo")
_OPCIONES_INVENTARIO = frozenset({"local", "sin_clientes"})


async def _marcada(cur, nombre: str) -> bool:
    await cur.execute("SELECT 1 FROM axioma_migracion_de_datos WHERE nombre = %s", (nombre,))
    return await cur.fetchone() is not None


async def _ejecutor_reglas_v1(cur) -> None:
    """Siembra las reglas del Ejecutor UNA vez (una desactivada por el admin no
    revive) y la edad máxima de un punto de restauración para C2 (sin pisar)."""
    await cur.execute(
        "INSERT IGNORE INTO axioma_config (config_key, config_value) VALUES ('ejecutor.c2_edad_max_s', '86400')")
    if await _marcada(cur, MIGRACION_EJECUTOR_REGLAS_V1):
        return
    for r in json.loads(_SEMILLA_EJECUTOR_REGLAS.read_text(encoding="utf-8")):
        await cur.execute(
            "INSERT IGNORE INTO ejecutor_regla (codigo, tipo, herramientas, campo, patron, ambito_host, "
            "ambito_roles, es_canario, origen, ejemplos_coincide, ejemplos_no_coincide) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (r["codigo"], r["tipo"], r["herramientas"], r["campo"], r["patron"], r["ambito_host"],
             ",".join(r["ambito_roles"]) or None, r["es_canario"], r["origen"],
             json.dumps(r["ejemplos_coincide"], ensure_ascii=False),
             json.dumps(r["ejemplos_no_coincide"], ensure_ascii=False)))
    await cur.execute("INSERT INTO axioma_migracion_de_datos (nombre) VALUES (%s)", (MIGRACION_EJECUTOR_REGLAS_V1,))


def parsear_inventario(texto: str) -> list[dict]:
    """`nombre:ip:puerto:rol[:opcion+opcion]` separados por coma. Sin espacios ni
    comillas: systemd (EnvironmentFile) y bash lo leen igual."""
    filas, nombres = [], set()
    for entrada in texto.split(","):
        partes = entrada.split(":")
        if len(partes) not in (4, 5) or not all(partes[:4]):
            raise ValueError("inventario_mal_formado")
        nombre, ip, puerto_txt, rol = partes[:4]
        opciones = set(partes[4].split("+")) if len(partes) == 5 else set()
        if not puerto_txt.isdigit() or not 0 < int(puerto_txt) < 65536:
            raise ValueError("inventario_puerto_invalido")
        if rol not in _ROLES_EJECUTOR or not opciones <= _OPCIONES_INVENTARIO or nombre in nombres:
            raise ValueError("inventario_valor_invalido")
        nombres.add(nombre)
        filas.append({"nombre": nombre, "ip": ip, "puerto": int(puerto_txt), "rol": rol,
                      "es_local": "local" in opciones, "con_datos_de_clientes": "sin_clientes" not in opciones})
    return filas


async def _ejecutor_inventario_v1(cur) -> None:
    """Las máquinas registradas en la Fase 0 (GO de Fernando por máquina, 2026-09-15),
    desde JAX_EJECUTOR_INVENTARIO. Sin la variable o mal formada, NO se marca: se
    reintenta en el próximo arranque."""
    if await _marcada(cur, MIGRACION_EJECUTOR_INVENTARIO_V1):
        return
    texto = os.environ.get("JAX_EJECUTOR_INVENTARIO", "").strip()
    if not texto:
        return
    try:
        filas = parsear_inventario(texto)
    except ValueError as exc:  # fail-soft: sin inventario la política no se exporta y el Ejecutor no arranca (cerrado); tumbar la plataforma por esto dejaría a la Mesa sin servicio
        logger.error("ejecutor_inventario_invalido codigo=%s", exc)
        return
    for f in filas:
        await cur.execute(
            "INSERT IGNORE INTO ejecutor_host (nombre, ip, puerto, rol, es_local, con_datos_de_clientes) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (f["nombre"], f["ip"], f["puerto"], f["rol"], f["es_local"], f["con_datos_de_clientes"]))
    await cur.execute("INSERT INTO axioma_migracion_de_datos (nombre) VALUES (%s)", (MIGRACION_EJECUTOR_INVENTARIO_V1,))

_EJECUTOR_CONFIG_C5 = (
    ("ejecutor.cerebro_faceta", "ejecutor"),
    ("ejecutor.auditor_faceta", "thot"),
    ("ejecutor.c5_lote_max", "20"),
    ("ejecutor.c5_intervalo_s", "15"),
    # El tope que usó U4 de la Fase 0 (scripts/ejecutor_fase0/auditor_costo.py).
    ("ejecutor.c5_max_tokens", "4000"),
    # Nace CERRADA: con el cerebro local todo auditor de otro proveedor es de nube, y la
    # Fase 0 prohibió que la nube vea datos de clientes. Abrirla es DECISIÓN de Fernando
    # (índice de SP1, punto 1 de «lo que el spec dice mal»). Cerrada, una misión que toca
    # una máquina con datos de clientes no arranca.
    ("ejecutor.c5_auditor_admite_datos_de_clientes", "false"),
)


async def _ejecutor_config_c5_v1(cur) -> None:
    """Configuración de C5 (auditor en vivo del Ejecutor). INSERT IGNORE: lo que el
    admin cambió no se pisa en el próximo arranque."""
    for clave, valor in _EJECUTOR_CONFIG_C5:
        await cur.execute("INSERT IGNORE INTO axioma_config (config_key, config_value) VALUES (%s, %s)", (clave, valor))


async def _indices_de_model_binding_proposal(cur) -> None:
    """PR-L ronda 2: los índices de list_proposals en una base donde la tabla
    ya existía sin ellos (en una base nueva los trae el CREATE). Idempotente."""
    for nombre, columnas in (("idx_created", "created_at"), ("idx_status_created", "status, created_at")):
        await cur.execute(
            "SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'model_binding_proposal' AND INDEX_NAME = %s LIMIT 1",
            (nombre,),
        )
        if await cur.fetchone() is None:
            await cur.execute(f"ALTER TABLE model_binding_proposal ADD INDEX {nombre} ({columnas})")


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

            for table_name, index_name, ddl in _INDEXES:
                if not await _index_exists(cur, table_name, index_name):
                    await cur.execute(ddl)
            await _indice_de_uso_por_periodo(cur)
            await _indice_de_cuentas_bloqueadas(cur)
            await _respaldo_de_uso(cur)

            await _drop_axioma_artifacts(cur)
            await _ajustes_que_mandan_v1(cur)
            await _ajuste_confirmar_costo_v1(cur)
            await _ejecutor_reglas_v1(cur)
            await _ejecutor_inventario_v1(cur)
            await _ejecutor_config_c5_v1(cur)
            await _seed_providers(cur)
            await _migrate_user_api_keys_to_credential(cur)
            await _seed_facets(cur)
            await _seed_provider_sync_config(cur)
            await _migrar_gemini_a_cabecera(cur)
            await _seed_models_and_backfill(cur)
            await _fix_anthropic_sonnet_alias(cur)
            await _seed_motors_and_capabilities(cur)
            await _seed_jax_local_motor(cur)
            await _seed_jax_local_has_tool_access(cur)
            await _seed_thot_motor(cur)
            await _seed_file_tools_capabilities(cur)
            await _fix_file_write_gate_and_auditor(cur)
            await _raise_generate_execution_ceiling(cur)
            await _motor_max_tokens_al_catalogo_v1(cur)
            # Después de TODAS las semillas de capability: las filas nuevas ya
            # entraron con su modo; las viejas se rellenan y la columna queda
            # VARCHAR(16) NOT NULL + CHECK. Una fila huérfana frena acá (ver
            # la función).
            await _backfill_capability_mode(cur)
            await _asegurar_forma_de_capability_mode(cur)
            await _eliminate_motor_model_ref_denormalization(cur)
            # Antes del seed de allowed_callers: kimi necesita el transporte
            # http_* para que tener acceso al gate tenga sentido.
            await _migrate_kimi_chat_transport(cur)
            await _seed_http_facet_allowed_callers(cur)
            # Después del bucle de _TABLES (la tabla existe) y antes de que
            # nadie escriba en ella (PR-L ronda 1).
            await _auditoria_de_catalogo_sin_fk_duras(cur)
            await _indices_de_model_binding_proposal(cur)
            # Ruling T6-6 (2026-09-15): idx_jacobs_pipelines_duenio NO se crea
            # aca -- jacobs_pipelines es del repo jax, y su indice vive en
            # jax/jacobs/store.py::init_tables(). La plataforma no corre DDL
            # sobre tablas de jax.
            # Despues de _seed_models_and_backfill: las filas de `model` tienen
            # que existir para poder actualizarlas.
            await _seed_model_max_tokens_param(cur)
            await _seed_model_max_output_tokens(cur)
            # Después de _asegurar_forma_de_capability_mode y de la columna de
            # _COLUMNS: las filas de capability existen con su forma final.
            await _semilla_min_output_tokens_v1(cur)
            # Requiere la columna contract_raw/grounding_snapshot ya creadas
            # arriba (bucle de _COLUMNS): idempotente, así que el orden solo
            # importa para que la columna exista, no para el contenido.
            await _reclassify_provenance_mismatch(cur)

        await conn.commit()
    # Regresión del 2026-09-12 (13:03-13:29): la migración `generate` 5 -> 15
    # corrió acá al arrancar jax-platform y LAS MANOS siguió con su catálogo
    # en memoria (techo 300 s) hasta que alguien lo reinició. El sello de
    # facet_resolver es la señal que LAS MANOS vigila; va DESPUÉS del commit.
    from facet_resolver import _tocar_sello
    _tocar_sello()
