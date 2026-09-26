CREATE TABLE IF NOT EXISTS aiia_scheduled_task (
  schedule_id varchar(64) NOT NULL COMMENT '稳定的定时任务标识',
  owner_user_id varchar(64) NOT NULL COMMENT '所属用户；所有读写与执行均按此字段授权',
  requirement_brief text NOT NULL COMMENT '用户确认后的自然语言任务说明',
  timezone varchar(64) NOT NULL DEFAULT 'Asia/Shanghai' COMMENT 'IANA 时区',
  cron_expr varchar(128) NOT NULL COMMENT '五段 cron 表达式',
  execution_plan_json json NOT NULL COMMENT '已确认、可执行的 Tool/Skill 顺序计划',
  enabled tinyint(1) NOT NULL DEFAULT 1 COMMENT '是否继续产生后续运行',
  revision_no int unsigned NOT NULL DEFAULT 1 COMMENT '计划修订号',
  next_run_at datetime(6) DEFAULT NULL COMMENT '下一次应运行时间，UTC；停用时为空',
  last_run_at datetime(6) DEFAULT NULL COMMENT '最近一次计划运行时间，UTC',
  idempotency_key varchar(128) DEFAULT NULL COMMENT '创建请求幂等键',
  created_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (schedule_id),
  UNIQUE KEY uk_schedule_owner_idempotency (owner_user_id, idempotency_key),
  KEY idx_schedule_owner_updated (owner_user_id, updated_at),
  KEY idx_schedule_due (enabled, next_run_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='用户 scope 内的服务端定时任务定义';
