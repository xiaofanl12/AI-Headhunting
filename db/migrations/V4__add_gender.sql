-- V4: 候选人补充性别字段（简历详情页与聊天卡片展示用）
-- 执行: python tools/apply_migration.py db/migrations/V4__add_gender.sql

USE headhunter;

-- 注意：FIRST/AFTER 必须放在列定义最末尾，COMMENT 要在 AFTER 之前
ALTER TABLE candidates
    ADD COLUMN gender VARCHAR(2) NULL
    COMMENT '性别：男/女，无法识别时为 NULL' AFTER name;
