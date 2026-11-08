-- AI 猎头智能助手 数据库结构
-- 执行: mysql -u root -p < db/schema.sql

CREATE DATABASE IF NOT EXISTS headhunter DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE headhunter;

CREATE TABLE IF NOT EXISTS companies (
    id            BIGINT PRIMARY KEY AUTO_INCREMENT,
    name          VARCHAR(128) NOT NULL UNIQUE,
    industry      VARCHAR(64),
    scale         VARCHAR(32)  COMMENT '如 500-2000人',
    introduction  TEXT         COMMENT '百科抓取或GLM生成',
    intro_source  TINYINT NULL DEFAULT NULL COMMENT '0=百科 1=GLM兜底；简介为空时为NULL',
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS positions (
    id            BIGINT PRIMARY KEY AUTO_INCREMENT,
    company_id    BIGINT NOT NULL,
    title         VARCHAR(128) NOT NULL,
    skill_tags    JSON     COMMENT '["Python","LangChain","RAG"]',
    min_years     INT DEFAULT 0,
    salary_range  VARCHAR(64),
    status        TINYINT DEFAULT 1 COMMENT '1=招聘中 0=关闭',
    KEY idx_status (status),
    FOREIGN KEY (company_id) REFERENCES companies(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS candidates (
    id            BIGINT PRIMARY KEY AUTO_INCREMENT,
    name          VARCHAR(64)  NOT NULL,
    gender        VARCHAR(2)   COMMENT '性别：男/女，无法识别时为 NULL',
    phone         VARCHAR(20),
    email         VARCHAR(128),
    resume_text   MEDIUMTEXT,
    skill_tags    JSON,
    years_of_exp  DECIMAL(4,1) DEFAULT 0,
    education     VARCHAR(32),
    status        VARCHAR(16) DEFAULT '待筛选'
                  COMMENT '待筛选/待推荐/已推荐/已联系/面试中/已入职/已放弃',
    data_source   VARCHAR(32) NOT NULL DEFAULT 'manual'
                  COMMENT 'manual=人工示例数据, resume_ner=公开简历语料',
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY idx_status (status),
    KEY idx_exp (years_of_exp),
    KEY idx_source (data_source)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS contact_records (
    id            BIGINT PRIMARY KEY AUTO_INCREMENT,
    candidate_id  BIGINT NOT NULL,
    position_id   BIGINT NOT NULL,
    match_score   DECIMAL(5,2),
    skill_score   DECIMAL(5,2),
    exp_score     DECIMAL(5,2),
    reason        TEXT COMMENT 'LLM生成的推荐理由',
    reason_src    VARCHAR(8) NOT NULL DEFAULT 'rule' COMMENT 'rule=规则模板 llm=模型润色 hr=HR手写',
    reason_updated_at DATETIME NULL,
    email_subject VARCHAR(255),
    email_body    MEDIUMTEXT,
    send_status   VARCHAR(16) DEFAULT 'pending' COMMENT 'pending/sent/failed',
    sent_at       DATETIME,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_cand_pos (candidate_id, position_id),
    KEY idx_send_status (send_status),
    FOREIGN KEY (candidate_id) REFERENCES candidates(id),
    FOREIGN KEY (position_id)  REFERENCES positions(id)
) ENGINE=InnoDB;

-- 公司分析留痕（评分依据可回溯）
CREATE TABLE IF NOT EXISTS company_analyses (
    id             BIGINT PRIMARY KEY AUTO_INCREMENT,
    company_id     BIGINT NOT NULL,
    score          INT          COMMENT '综合评分 0-100（由可核验数据计算）',
    level          VARCHAR(16)  COMMENT '重点合作/可以推进/谨慎评估/暂缓推荐',
    summary        VARCHAR(255),
    strengths      MEDIUMTEXT   COMMENT 'JSON 数组：优势',
    risks          MEDIUMTEXT   COMMENT 'JSON 数组：风险与待核实项',
    fit_profiles   MEDIUMTEXT   COMMENT 'JSON 数组：适合推送的候选人画像',
    questions      MEDIUMTEXT   COMMENT 'JSON 数组：建议向企业确认的问题',
    verdict        MEDIUMTEXT   COMMENT '综合建议',
    facts_snapshot JSON         COMMENT '打分时的数据快照',
    model          VARCHAR(64)  COMMENT '生成结论的模型或规则引擎',
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY idx_company (company_id),
    FOREIGN KEY (company_id) REFERENCES companies(id)
) ENGINE=InnoDB;

-- 辅助审计表
CREATE TABLE IF NOT EXISTS crawl_logs (
    id            BIGINT PRIMARY KEY AUTO_INCREMENT,
    url           VARCHAR(512),
    source        VARCHAR(32),
    status        VARCHAR(16) COMMENT 'success/failed',
    rows_crawled  INT DEFAULT 0,
    cost_ms       INT,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS query_logs (
    id            BIGINT PRIMARY KEY AUTO_INCREMENT,
    session_id    VARCHAR(64),
    user_input    TEXT,
    generated_sql TEXT,
    executed      TINYINT DEFAULT 0,
    affected_rows INT DEFAULT 0,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- 最小权限业务账号（可选，推荐）
-- CREATE USER 'headhunter'@'%' IDENTIFIED BY '强密码';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON headhunter.* TO 'headhunter'@'%';
