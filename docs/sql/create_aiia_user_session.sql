CREATE TABLE IF NOT EXISTS aiia_user_session (
  session_id bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '会话主键',
  session_token varchar(128) NOT NULL COMMENT '会话 token',
  user_id varchar(64) NOT NULL COMMENT '关联用户',
  session_type varchar(32) NOT NULL DEFAULT 'guest' COMMENT '会话类型：guest/login/api',
  status varchar(32) NOT NULL DEFAULT 'active' COMMENT '状态：active/expired/revoked',
  user_agent varchar(255) NOT NULL DEFAULT '' COMMENT '浏览器或客户端 UA 摘要',
  ip_address varchar(64) NOT NULL DEFAULT '' COMMENT '客户端 IP 摘要',
  expires_at datetime DEFAULT NULL COMMENT '过期时间',
  last_seen_at datetime DEFAULT NULL COMMENT '最后访问时间',
  metadata_json json DEFAULT NULL COMMENT '会话扩展信息',
  created_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (session_id),
  UNIQUE KEY uk_session_token (session_token),
  KEY idx_user_status (user_id, status),
  KEY idx_last_seen_at (last_seen_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='用户登录与 guest 会话表；后续可承载微信登录态';
