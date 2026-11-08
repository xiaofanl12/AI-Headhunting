-- V5：公司分析结果留痕
-- 用途：每次「公司分析」落一条记录，便于回溯评分依据、避免重复调用大模型

USE headhunter;

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
