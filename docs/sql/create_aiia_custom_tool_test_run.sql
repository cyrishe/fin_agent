CREATE TABLE IF NOT EXISTS aiia_custom_tool_test_run (
  test_run_id bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '测试运行主键',
  artifact_id bigint unsigned NOT NULL COMMENT '关联 aiia_runtime_artifact.artifact_id',
  revision_no int unsigned NOT NULL COMMENT '被测试的实现修订号',
  test_kind varchar(32) NOT NULL DEFAULT 'sample_smoke' COMMENT 'sample_smoke/fixture/real_data/regression',
  status varchar(16) NOT NULL COMMENT 'passed/failed/blocked',
  execution_ok tinyint(1) NOT NULL DEFAULT 0 COMMENT '沙箱是否成功执行',
  contract_ok tinyint(1) NOT NULL DEFAULT 0 COMMENT '输出是否符合 Schema',
  business_ok tinyint(1) NOT NULL DEFAULT 0 COMMENT '业务结果是否满足预期且非错误载荷',
  input_json longtext DEFAULT NULL COMMENT '测试输入快照',
  output_json longtext DEFAULT NULL COMMENT '测试输出快照',
  error_text text DEFAULT NULL COMMENT '安全化错误摘要',
  diagnostics_json longtext DEFAULT NULL COMMENT '执行诊断，不存密钥',
  created_by varchar(64) NOT NULL DEFAULT '' COMMENT '发起测试的用户或系统',
  created_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '测试时间',
  PRIMARY KEY (test_run_id),
  KEY idx_custom_tool_revision (artifact_id, revision_no, created_at),
  KEY idx_custom_tool_status (artifact_id, status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='自定义工具实现修订的独立测试证据；启用门禁以三项 ok 为准';
