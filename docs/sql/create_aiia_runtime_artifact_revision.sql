CREATE TABLE IF NOT EXISTS aiia_runtime_artifact_revision (
  revision_id bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '修订主键',
  artifact_id bigint unsigned NOT NULL COMMENT '关联 aiia_runtime_artifact.artifact_id',
  revision_no int unsigned NOT NULL COMMENT '修订号，从 1 开始递增',
  source_type varchar(32) NOT NULL DEFAULT 'ui' COMMENT '来源：ui/file_sync/api/system',
  definition_json longtext DEFAULT NULL COMMENT '定义文件快照，如 tool.json/skill.json',
  schema_json longtext DEFAULT NULL COMMENT 'schema 快照',
  spec_json longtext DEFAULT NULL COMMENT 'spec 快照',
  markdown_text longtext DEFAULT NULL COMMENT 'SKILL.md 或补充文档快照',
  content_hash varchar(64) NOT NULL DEFAULT '' COMMENT '内容哈希',
  change_summary varchar(255) NOT NULL DEFAULT '' COMMENT '变更摘要',
  created_by varchar(64) NOT NULL DEFAULT '' COMMENT '修订提交人或系统',
  created_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (revision_id),
  UNIQUE KEY uniq_artifact_revision (artifact_id, revision_no),
  KEY idx_artifact_created (artifact_id, created_at),
  KEY idx_content_hash (content_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='运行时资产修订历史表；保存工具与技能定义的版本快照';
