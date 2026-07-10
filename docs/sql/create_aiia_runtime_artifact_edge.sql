CREATE TABLE IF NOT EXISTS aiia_runtime_artifact_edge (
  edge_id bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '依赖边主键',
  from_artifact_id bigint unsigned NOT NULL COMMENT '起点资产',
  to_artifact_id bigint unsigned NOT NULL COMMENT '终点资产',
  edge_type varchar(32) NOT NULL COMMENT '关系类型：uses/depends_on/handoff_to/includes/emits',
  edge_order int NOT NULL DEFAULT 0 COMMENT '顺序号，用于 workflow/skill 编排',
  condition_text varchar(255) NOT NULL DEFAULT '' COMMENT '触发条件或说明',
  metadata_json json DEFAULT NULL COMMENT '补充元信息',
  enabled tinyint(1) NOT NULL DEFAULT 1 COMMENT '该依赖是否启用',
  created_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (edge_id),
  UNIQUE KEY uniq_artifact_edge (from_artifact_id, to_artifact_id, edge_type, edge_order),
  KEY idx_from_edge (from_artifact_id, edge_type, enabled),
  KEY idx_to_edge (to_artifact_id, edge_type, enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='运行时资产依赖图边表；用于技能、工作流和工具编排关系管理';
