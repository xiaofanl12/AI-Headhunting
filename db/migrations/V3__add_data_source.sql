-- 数据来源标注：区分人工构造的示例数据与真实公开语料，保证数据可溯源
USE headhunter;

ALTER TABLE candidates
  ADD COLUMN data_source VARCHAR(32) NOT NULL DEFAULT 'manual'
  COMMENT 'manual=人工示例数据, resume_ner=公开简历语料(ResumeNER)';

ALTER TABLE candidates
  ADD KEY idx_source (data_source);
