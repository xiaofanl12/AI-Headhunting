-- V10: 推送候选人时携带推选理由
-- 1) reason_src 记录理由来源：rule=规则模板 / llm=大模型润色 / hr=HR 手改
--    便于回答「这条理由是人写的还是机器写的」，避免 LLM 幻觉被当成结论
ALTER TABLE contact_records
  ADD COLUMN reason_src VARCHAR(8) NOT NULL DEFAULT 'rule'
  COMMENT '理由来源: rule规则模板/llm大模型润色/hr HR手改';

-- 2) reason_updated_at 理由最后更新时间（重算会刷新理由，需要留痕）
ALTER TABLE contact_records
  ADD COLUMN reason_updated_at DATETIME NULL COMMENT '推荐理由最后更新时间';

-- 3) 历史数据补齐来源标记：已有理由的一律视为规则模板产出
UPDATE contact_records SET reason_src = 'rule', reason_updated_at = created_at
 WHERE reason IS NOT NULL AND reason <> '' AND reason_updated_at IS NULL;
