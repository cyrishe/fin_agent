CREATE TABLE IF NOT EXISTS aiia_scheduled_task_run (
  run_id varchar(64) NOT NULL COMMENT '单次运行标识',
  schedule_id varchar(64) NOT NULL COMMENT '来源定时任务',
  owner_user_id varchar(64) NOT NULL COMMENT '运行所属用户；执行时再次用于授权',
  schedule_revision_no int unsigned NOT NULL COMMENT '运行采用的任务修订号',
  requirement_brief text NOT NULL COMMENT '运行时的自然语言说明快照',
  execution_plan_json json NOT NULL COMMENT '运行时的执行计划快照',
  scheduled_for datetime(6) NOT NULL COMMENT '本次计划时间，UTC',
  status varchar(32) NOT NULL DEFAULT 'pending' COMMENT 'pending/running/completed/failed',
  lease_owner varchar(128) DEFAULT NULL COMMENT '当前 worker 标识',
  lease_until datetime(6) DEFAULT NULL COMMENT '运行租约截止时间，UTC',
  result_json json DEFAULT NULL COMMENT '步骤结果与引用',
  error_text text DEFAULT NULL COMMENT '失败原因',
  started_at datetime(6) DEFAULT NULL,
  finished_at datetime(6) DEFAULT NULL,
  created_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (run_id),
  UNIQUE KEY uk_schedule_run_slot (schedule_id, scheduled_for),
  KEY idx_run_claim (status, lease_until, scheduled_for),
  KEY idx_run_owner_created (owner_user_id, created_at),
  CONSTRAINT fk_scheduled_run_schedule
    FOREIGN KEY (schedule_id) REFERENCES aiia_scheduled_task(schedule_id)
    ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='定时任务的持久化运行记录与 worker 租约';
