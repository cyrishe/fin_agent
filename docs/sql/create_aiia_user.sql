CREATE TABLE IF NOT EXISTS aiia_user (
  user_id varchar(64) NOT NULL COMMENT '用户主键；支持 guest_*/wx_* 等稳定标识',
  user_type varchar(32) NOT NULL DEFAULT 'guest' COMMENT '用户类型：guest/member/admin/system',
  display_name varchar(128) NOT NULL DEFAULT '' COMMENT '显示名称',
  status varchar(32) NOT NULL DEFAULT 'active' COMMENT '状态：active/disabled/deleted',
  profile_json json DEFAULT NULL COMMENT '轻量用户画像与扩展字段',
  created_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (user_id),
  KEY idx_user_type_status (user_type, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='平台用户表；承载 guest、登录用户和系统用户';
