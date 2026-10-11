-- SUPERSEDED DESIGN DRAFT. Do not deploy these old aiia_stock_technical_* names.
-- Executed canonical migration: docs/sql/create_aiia_stock_indicator_and_lhb.sql.
-- Runtime receipt: 15_implementation_receipt.md.
-- REVIEW DRAFT: MySQL 8.0 / existing kingdomai market-data database.
-- This file has NOT been executed. Deploy as an explicit migration, never from
-- a request handler or provider constructor. Existing source tables stay intact.
-- Standard feature bundle: technical_daily_v1 (27 columns). The earlier pure
-- price_volume_v1 function remains compatible; production registration is pending.
-- Market dates/bar timestamps: Asia/Shanghai. Operational timestamps: UTC.
-- MA reuse/capacity decision: see 13_ma_reuse_and_capacity.md. This wide table
-- includes MA; do not expand legacy aiia_indicator_series into a second full
-- stock-feature store or keep duplicate standard MA writers after cutover.
-- Legacy MA deployment is unverified (absent from the currently configured DB).
-- 6000 stocks * 21 trading days = 126000 result rows/month, per retained copy.
-- Batch rows are per shared publication, not per stock or per requesting user.
-- Validate month/year workloads and evidence-file storage before migration.

CREATE TABLE aiia_stock_technical_batch (
    batch_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    universe_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    trade_date DATE NOT NULL,
    formula_revision VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    price_basis VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_name VARCHAR(64) NOT NULL,
    source_watermark DATETIME(6) NULL,
    input_manifest_ref VARCHAR(512) NOT NULL,
    requested_count INT UNSIGNED NOT NULL,
    written_count INT UNSIGNED NOT NULL DEFAULT 0,
    started_at DATETIME(6) NOT NULL,
    published_at DATETIME(6) NULL,
    PRIMARY KEY (batch_id),
    KEY idx_technical_published (universe_key, trade_date, formula_revision, price_basis, published_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE aiia_stock_technical_daily (
    batch_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    stk_code VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    input_start_time DATETIME NOT NULL,
    input_end_time DATETIME NOT NULL,
    input_fingerprint CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    bar_count INT UNSIGNED NOT NULL,
    ma5 DOUBLE NULL,
    ma10 DOUBLE NULL,
    ma20 DOUBLE NULL,
    ma60 DOUBLE NULL,
    return1 DOUBLE NULL,
    return5 DOUBLE NULL,
    return20 DOUBLE NULL,
    prior_volume_mean5 DOUBLE NULL,
    prior_volume_mean20 DOUBLE NULL,
    volume_ratio5 DOUBLE NULL,
    bias20 DOUBLE NULL,
    prior_close_high20 DOUBLE NULL,
    prior_close_high60 DOUBLE NULL,
    prior_close_low20 DOUBLE NULL,
    distance_prior_high20 DOUBLE NULL,
    macd_dif DOUBLE NULL,
    macd_dea DOUBLE NULL,
    macd_hist DOUBLE NULL,
    rsi14 DOUBLE NULL,
    atr14 DOUBLE NULL,
    atr_ratio14 DOUBLE NULL,
    boll_mid20 DOUBLE NULL,
    boll_upper20 DOUBLE NULL,
    boll_lower20 DOUBLE NULL,
    boll_width20 DOUBLE NULL,
    boll_percent_b20 DOUBLE NULL,
    volatility20 DOUBLE NULL,
    PRIMARY KEY (batch_id, stk_code),
    KEY idx_technical_security (stk_code, batch_id),
    CONSTRAINT fk_technical_batch FOREIGN KEY (batch_id)
        REFERENCES aiia_stock_technical_batch (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE aiia_stock_lhb_event (
    event_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    trade_date DATE NOT NULL,
    stk_code VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    reason_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    reason_text TEXT NOT NULL,
    period_start DATE NULL,
    period_end DATE NOT NULL,
    provider VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_reason_code VARCHAR(128) NULL,
    source_url VARCHAR(1024) NULL,
    source_payload_ref VARCHAR(512) NULL,
    parser_revision VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    fetched_at DATETIME(6) NOT NULL,
    PRIMARY KEY (event_id),
    UNIQUE KEY uq_lhb_reason (trade_date, stk_code, reason_key),
    KEY idx_lhb_security (stk_code, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE aiia_stock_lhb_seat (
    event_id BIGINT UNSIGNED NOT NULL,
    side VARCHAR(4) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    rank_no SMALLINT UNSIGNED NOT NULL,
    source_seat_code VARCHAR(64) NULL,
    seat_name VARCHAR(255) NOT NULL,
    buy_amount DECIMAL(24,4) NULL,
    sell_amount DECIMAL(24,4) NULL,
    net_amount DECIMAL(24,4) NULL,
    PRIMARY KEY (event_id, side, rank_no),
    CONSTRAINT fk_lhb_event FOREIGN KEY (event_id)
        REFERENCES aiia_stock_lhb_event (event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Candidate only: verify EXPLAIN and migration cost before executing an index
-- change on aiia_stock_realtime_minute_snapshot_full. The read-only audit found
-- only PRIMARY(trade_date,kline_type,bar_end_time,stk_code).
-- Proposed access index: (stk_code,kline_type,trade_date,bar_end_time).
-- Retain source-specific seat IDs; never treat them as cross-provider identities.
-- side is the disclosed BUY/SELL list, not an account's transaction direction.
-- Rollback means disabling new jobs/providers and using the prior published
-- batch. Do not DROP these tables as a routine rollback step.
