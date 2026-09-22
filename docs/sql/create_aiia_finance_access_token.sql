-- Managed Finance API access tokens. Apply to SYSTEM_DB_URL's database.
-- Timestamps are UTC. expires_at IS NULL explicitly means never expires.
-- The complete token is never stored; token_digest is used for verification.
CREATE TABLE IF NOT EXISTS `aiia_finance_access_token` (
  `token_id` char(32) NOT NULL,
  `project_name` varchar(128) NOT NULL,
  `token_name` varchar(128) NOT NULL,
  `principal_id` varchar(64) NOT NULL,
  `token_digest` binary(32) NOT NULL,
  `token_prefix` varchar(24) NOT NULL,
  `token_suffix` varchar(12) NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `expires_at` datetime(6) DEFAULT NULL,
  `disabled_at` datetime(6) DEFAULT NULL,
  `disabled_reason` varchar(255) DEFAULT NULL,
  `created_by` varchar(64) DEFAULT NULL,
  `disabled_by` varchar(64) DEFAULT NULL,
  PRIMARY KEY (`token_id`),
  UNIQUE KEY `uk_finance_access_token_digest` (`token_digest`),
  KEY `idx_finance_access_token_project` (`project_name`, `token_name`, `created_at`),
  KEY `idx_finance_access_token_principal_state` (`principal_id`, `disabled_at`, `expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
