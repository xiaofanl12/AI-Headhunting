-- V8: 简历抽屉扩展字段 —— 教育背景 / 荣誉证书 / 工作经验（JSON 数组）
ALTER TABLE candidates
    ADD COLUMN edu_background   JSON NULL COMMENT '教育背景 [{school,major,degree,period}]',
    ADD COLUMN honors           JSON NULL COMMENT '荣誉证书 [字符串]',
    ADD COLUMN work_experience  JSON NULL COMMENT '工作经验 [{company,role,period,desc}]';
