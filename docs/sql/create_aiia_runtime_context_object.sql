CREATE TABLE IF NOT EXISTS aiia_runtime_context_object (
  context_id bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '上下文对象主键',
  thread_id bigint unsigned DEFAULT NULL COMMENT '所属线程',
  task_id bigint unsigned DEFAULT NULL COMMENT '所属任务',
  turn_id bigint unsigned DEFAULT NULL COMMENT '所属轮次',
  context_type varchar(64) NOT NULL COMMENT '上下文类型：tool_result/summary/analysis/report_snapshot',
  object_key varchar(128) NOT NULL DEFAULT '' COMMENT '对象键，如 momentum_snapshot_20260330_k4',
  content_json longtext DEFAULT NULL COMMENT '结构化内容',
  summary_text text DEFAULT NULL COMMENT '摘要文本',
  retention_level varchar(32) NOT NULL DEFAULT 'normal' COMMENT '保留级别：low/normal/high',
  created_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (context_id),
  UNIQUE KEY uniq_thread_context_key (thread_id, context_type, object_key),
  KEY idx_task_context (task_id, context_type),
  KEY idx_turn_context (turn_id, context_type),
  KEY idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='运行时上下文对象表；保存可复用的结构化结果与摘要';
