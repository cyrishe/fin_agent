-- Shared failed-authentication limiter.
--
-- `subject_hash` and `remote_addr_hash` are HMAC-SHA256 values. The table
-- never stores a plaintext phone number or client IP address. A row counts as
-- a failed/in-flight attempt while `succeeded_at` is NULL; successful logins
-- are marked without introducing a status enum. Coarse per-IP and global
-- request budgets count all rows, including successful attempts, before
-- password hashing or member-session creation.
--
-- Retention is explicit rather than coupled to login requests. Invoke
-- `AuthRateLimitService.cleanup_old_attempts()` from an external maintenance
-- job in bounded batches; `idx_aiia_auth_attempt_created_at` serves that scan.

CREATE TABLE IF NOT EXISTS aiia_auth_attempt (
  attempt_id VARCHAR(64) NOT NULL,
  action VARCHAR(64) NOT NULL,
  subject_hash CHAR(64) NOT NULL,
  remote_addr_hash CHAR(64) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  succeeded_at DATETIME(6) NULL,
  PRIMARY KEY (attempt_id),
  KEY idx_aiia_auth_attempt_subject (
    action,
    subject_hash,
    succeeded_at,
    created_at
  ),
  KEY idx_aiia_auth_attempt_remote_addr (
    action,
    remote_addr_hash,
    succeeded_at,
    created_at
  ),
  KEY idx_aiia_auth_attempt_remote_addr_all (
    action,
    remote_addr_hash,
    created_at
  ),
  KEY idx_aiia_auth_attempt_action_created_at (
    action,
    created_at
  ),
  KEY idx_aiia_auth_attempt_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
