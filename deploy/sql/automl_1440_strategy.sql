-- Strategy definitions are versioned by strategy_id. A changed formula gets a
-- new ID so old selections retain their exact interpretation.
CREATE TABLE IF NOT EXISTS automl_strategy_definition (
  user_id VARCHAR(128) NOT NULL,
  strategy_id VARCHAR(128) NOT NULL,
  title VARCHAR(255) NOT NULL,
  summary TEXT NOT NULL,
  training_method TEXT NOT NULL,
  selection_formula TEXT NOT NULL,
  execution_formula TEXT NOT NULL,
  formula_json JSON NOT NULL,
  definition_sha256 CHAR(64) NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (user_id, strategy_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Both model ranks are retained even when the rank-1 turnover gate means no
-- trade. This is an inference record, not an order or fill record.
CREATE TABLE IF NOT EXISTS automl_strategy_selection (
  user_id VARCHAR(128) NOT NULL,
  strategy_id VARCHAR(128) NOT NULL,
  signal_date DATE NOT NULL,
  selection_rank TINYINT UNSIGNED NOT NULL,
  stock_code CHAR(6) NOT NULL,
  stock_name VARCHAR(128) NOT NULL,
  signal_price DECIMAL(18,4) NOT NULL,
  signal_return DECIMAL(12,8) NOT NULL,
  predicted_class VARCHAR(16) NOT NULL,
  p_lt0 DECIMAL(12,8) NOT NULL,
  p_0to1 DECIMAL(12,8) NOT NULL,
  p_1to3 DECIMAL(12,8) NOT NULL,
  p_ge3 DECIMAL(12,8) NOT NULL,
  volume_ratio DECIMAL(18,8) NULL,
  turnover_so_far_pct DECIMAL(18,8) NULL,
  float_mv_100m_cny DECIMAL(18,6) NULL,
  volume_4of5_increasing TINYINT NULL,
  ma_bull_5_10_20 TINYINT NULL,
  all_intraday_lows_above_ma TINYINT NULL,
  amount_1440_cny DECIMAL(22,2) NULL,
  amount_prior20_median_cny DECIMAL(22,2) NULL,
  amount_prior20_valid_days TINYINT UNSIGNED NOT NULL,
  turnover_gate_pass TINYINT(1) NOT NULL,
  actionable TINYINT(1) NOT NULL,
  candidate_count INT UNSIGNED NOT NULL,
  training_rows INT UNSIGNED NOT NULL,
  training_dates_json JSON NOT NULL,
  features_json JSON NOT NULL,
  context_json JSON NOT NULL,
  quote_source VARCHAR(128) NOT NULL,
  signal_capture_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (user_id, strategy_id, signal_date, selection_rank),
  KEY idx_automl_selection_stock_date (stock_code, signal_date),
  CONSTRAINT fk_automl_selection_strategy FOREIGN KEY (user_id, strategy_id)
    REFERENCES automl_strategy_definition (user_id, strategy_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
