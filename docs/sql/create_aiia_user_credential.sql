CREATE TABLE IF NOT EXISTS aiia_user_credential (
  credential_id bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '登录凭据主键',
  user_id varchar(64) NOT NULL COMMENT '关联用户；不承载手机号等外部身份',
  credential_type varchar(32) NOT NULL DEFAULT 'password' COMMENT '凭据类型；当前仅 password',
  credential_hash varchar(512) NOT NULL COMMENT '带盐的单向密码哈希',
  created_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (credential_id),
  UNIQUE KEY uk_user_credential_type (user_id, credential_type),
  CONSTRAINT fk_user_credential_user
    FOREIGN KEY (user_id) REFERENCES aiia_user (user_id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='用户登录凭据；与外部身份和业务用户信息分离';
