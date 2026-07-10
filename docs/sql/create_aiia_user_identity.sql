CREATE TABLE IF NOT EXISTS aiia_user_identity (
  identity_id bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '身份记录主键',
  user_id varchar(64) NOT NULL COMMENT '关联用户',
  identity_type varchar(32) NOT NULL COMMENT '身份类型：wechat_openid/wechat_unionid/email/phone',
  identity_value varchar(191) NOT NULL COMMENT '身份值',
  is_primary tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否主身份',
  metadata_json json DEFAULT NULL COMMENT '身份扩展信息',
  created_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (identity_id),
  UNIQUE KEY uk_identity_type_value (identity_type, identity_value),
  KEY idx_user_identity (user_id, identity_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='用户外部身份绑定表；后续接微信登录使用';
