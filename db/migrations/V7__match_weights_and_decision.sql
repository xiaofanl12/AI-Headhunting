-- V7: 匹配权重配置 + HR 人工决策字段
-- 1) 匹配权重持久化（单行键值，HR 可在界面调整）
CREATE TABLE IF NOT EXISTS match_settings (
  skey VARCHAR(32) PRIMARY KEY,
  sval VARCHAR(64) NOT NULL,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='匹配引擎可调参数';

-- 2) 推荐记录增加 HR 决策：NULL=待审阅；'邀约' / '暂缓'
--    AI 匹配只产生标记，最终邀约由 HR 查阅详细简历后决定
ALTER TABLE contact_records
  ADD COLUMN hr_decision VARCHAR(8) NULL COMMENT 'HR决策: 邀约/暂缓/NULL待审阅';

INSERT INTO match_settings (skey, sval) VALUES ('skill_weight', '0.7');
