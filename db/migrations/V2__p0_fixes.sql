-- P0 修复迁移：唯一约束 + 查询索引
-- 执行方式：Navicat 打开本文件运行，或 python tools/apply_migration.py db/migrations/V2__p0_fixes.sql
-- 说明：执行前已确认 contact_records 无 (candidate_id, position_id) 重复数据

USE headhunter;

-- 1. 防重复推荐：同一候选人对同一职位只允许一条推荐记录
ALTER TABLE contact_records
  ADD UNIQUE KEY uk_cand_pos (candidate_id, position_id);

-- 2. 高频查询索引：匹配时按 status 过滤、按经验排序、按发送状态扫描
ALTER TABLE candidates
  ADD KEY idx_status (status),
  ADD KEY idx_exp (years_of_exp);

ALTER TABLE contact_records
  ADD KEY idx_send_status (send_status);

ALTER TABLE positions
  ADD KEY idx_status (status);

-- 3. 修正来源字段矛盾：简介为空时不应标注来源
ALTER TABLE companies
  MODIFY intro_source TINYINT NULL DEFAULT NULL;
