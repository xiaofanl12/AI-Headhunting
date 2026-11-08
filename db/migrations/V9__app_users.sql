-- V9: 系统登录用户（HR 账号）
CREATE TABLE IF NOT EXISTS app_users (
    id            BIGINT PRIMARY KEY AUTO_INCREMENT,
    username      VARCHAR(32)  NOT NULL COMMENT '登录名',
    display_name  VARCHAR(32)  NOT NULL COMMENT '显示名',
    password_hash VARCHAR(255) NOT NULL COMMENT 'PBKDF2-SHA256 含盐',
    role          VARCHAR(16)  NOT NULL DEFAULT 'hr' COMMENT 'hr/admin',
    last_login_at DATETIME     NULL,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_username (username)
) ENGINE=InnoDB;
