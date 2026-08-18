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
]

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

        await conn.commit()
