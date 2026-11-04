-- Training packages are immutable by experiment_id. A new package revision gets
-- a new ID; cloud_verified_at stays NULL until an independent cloud readback.
CREATE TABLE IF NOT EXISTS automl_training_archive (
  user_id VARCHAR(128) NOT NULL,
  experiment_id VARCHAR(128) NOT NULL,
  experiment_date DATE NOT NULL,
  title VARCHAR(255) NOT NULL,
  category_path VARCHAR(255) NOT NULL,
  local_path TEXT NOT NULL,
  cloud_path TEXT NOT NULL,
  source_commit CHAR(40) NOT NULL,
  manifest_sha256 CHAR(64) NOT NULL,
  file_count INT UNSIGNED NOT NULL,
  local_verified_at DATETIME(6) NOT NULL,
  cloud_verified_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (user_id, experiment_id),
  KEY idx_training_archive_date (experiment_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
