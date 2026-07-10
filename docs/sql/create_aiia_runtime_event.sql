CREATE TABLE IF NOT EXISTS aiia_runtime_event (
  event_id bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '事件主键',
  thread_id bigint unsigned NOT NULL COMMENT '所属线程',
  turn_id bigint unsigned DEFAULT NULL COMMENT '关联轮次',
  task_id bigint unsigned DEFAULT NULL COMMENT '关联任务',
  sequence_no bigint unsigned NOT NULL COMMENT '在线程内严格递增的事件序号',
  event_type varchar(64) NOT NULL COMMENT '事件类型：user_message/assistant_message/tool_call/tool_result/checkpoint/...',
  actor_type varchar(32) NOT NULL DEFAULT '' COMMENT '事件角色：user/assistant/tool/skill/system/agent',
  actor_id varchar(64) NOT NULL DEFAULT '' COMMENT '事件角色标识',
  payload_json longtext DEFAULT NULL COMMENT '事件载荷',
  summary_text text DEFAULT NULL COMMENT '事件摘要',
  created_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (event_id),
  UNIQUE KEY uniq_thread_sequence (thread_id, sequence_no),
  KEY idx_thread_created (thread_id, created_at),
  KEY idx_turn_event (turn_id, event_type),
  KEY idx_task_event (task_id, event_type),
  KEY idx_event_type_created (event_type, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='运行时事件流表；append-only 保存对话、工具调用和状态变化轨迹';
