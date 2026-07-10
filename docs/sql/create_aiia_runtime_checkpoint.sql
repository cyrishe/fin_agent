CREATE TABLE IF NOT EXISTS aiia_runtime_checkpoint (
  checkpoint_id bigint unsigned NOT NULL AUTO_INCREMENT COMMENT 'checkpoint 主键',
  thread_id bigint unsigned NOT NULL COMMENT '所属线程',
  turn_id bigint unsigned DEFAULT NULL COMMENT '关联轮次',
  task_id bigint unsigned DEFAULT NULL COMMENT '关联任务',
  source_event_id bigint unsigned DEFAULT NULL COMMENT '触发 checkpoint 的事件 ID',
  namespace varchar(64) NOT NULL DEFAULT 'default' COMMENT '命名空间，用于区分不同子流程',
  state_json longtext DEFAULT NULL COMMENT '状态快照',
  state_summary text DEFAULT NULL COMMENT '状态摘要',
  created_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (checkpoint_id),
  KEY idx_thread_created (thread_id, created_at),
  KEY idx_task_created (task_id, created_at),
  KEY idx_source_event (source_event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='运行时 checkpoint 表；用于线程与任务状态恢复';
