-- V6: 企查查合规风险核查结果缓存
-- 目的：三个核查接口是计费的（免费额度 20 次/接口），同一家企业 30 天内不重复查，
--       同时保留历史，便于回答「这条风险信息是什么时候核的」。

CREATE TABLE IF NOT EXISTS company_risk_checks (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    company_id      BIGINT       NULL     COMMENT 'companies.id，名称未匹配时为 NULL',
    company_name    VARCHAR(128) NOT NULL COMMENT '查询时使用的企业名称',
    shixin_count    INT          NOT NULL DEFAULT 0 COMMENT '失信记录条数',
    exception_count INT          NOT NULL DEFAULT 0 COMMENT '经营异常记录条数',
    penalty_count   INT          NOT NULL DEFAULT 0 COMMENT '行政处罚记录条数',
    detail          JSON         NULL     COMMENT 'qcc_client.risk_check 的完整结果',
    error           VARCHAR(255) NOT NULL DEFAULT '' COMMENT '整体失败原因，成功时为空',
    checked_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_risk_company (company_id, checked_at),
    KEY idx_risk_name (company_name, checked_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='企查查合规风险核查缓存';
