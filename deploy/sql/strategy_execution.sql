-- Human-readable trading strategy records. Existing automl_* tables remain
-- available during migration; this schema does not alter deployed jobs.
CREATE TABLE IF NOT EXISTS strategy_daily_run (
  user_id VARCHAR(128) NOT NULL,
  strategy_name VARCHAR(64) NOT NULL,
  model_version VARCHAR(24) NOT NULL,
  version_desc VARCHAR(160) NOT NULL,
  signal_date DATE NOT NULL,
  signal_at DATETIME(6) NULL,
  candidate_count INT UNSIGNED NULL,
  selection_count INT UNSIGNED NOT NULL,
  model_sha256 CHAR(64) NULL,
  environment_json JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (user_id, strategy_name, model_version, signal_date),
  KEY idx_strategy_daily_run_date (user_id, signal_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS strategy_execution (
  user_id VARCHAR(128) NOT NULL,
  strategy_name VARCHAR(64) NOT NULL,
  model_version VARCHAR(24) NOT NULL,
  version_desc VARCHAR(160) NOT NULL,
  signal_date DATE NOT NULL,
  selection_rank SMALLINT UNSIGNED NOT NULL,
  stock_code CHAR(6) NOT NULL,
  stock_name VARCHAR(128) NOT NULL,
  actionable TINYINT(1) NOT NULL DEFAULT 1,
  signal_at DATETIME(6) NULL,
  signal_price DECIMAL(18,4) NULL,
  buy_at DATETIME(6) NULL,
  buy_price DECIMAL(18,4) NULL,
  buy_price_basis VARCHAR(80) NULL,
  sell_at DATETIME(6) NULL,
  sell_price DECIMAL(18,4) NULL,
  sell_price_basis VARCHAR(80) NULL,
  gross_return_pct DECIMAL(14,8) NULL,
  net_return_pct DECIMAL(14,8) NULL,
  details_json JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (user_id, strategy_name, model_version, signal_date, selection_rank),
  KEY idx_strategy_execution_date (user_id, signal_date),
  KEY idx_strategy_execution_pending (user_id, strategy_name, actionable, sell_price, signal_date),
  CONSTRAINT fk_strategy_execution_run FOREIGN KEY
    (user_id, strategy_name, model_version, signal_date)
    REFERENCES strategy_daily_run (user_id, strategy_name, model_version, signal_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
