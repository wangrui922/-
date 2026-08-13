-- 007 · questions 表新增 ship_ready 列（版权护栏 · T07） · PostgreSQL 方言
--
-- ship_ready：是否可随产品对外发布。
--   默认 TRUE（可发布）；真实考卷若版权未清/仅内部评测，导入时显式置 FALSE，
--   使其不进入任何对外发布/导出池（仅内部校验、讲解质量评测可用）。
-- 存量题默认可发布（TRUE）。

ALTER TABLE questions ADD COLUMN ship_ready BOOLEAN NOT NULL DEFAULT TRUE;

UPDATE questions SET ship_ready = TRUE WHERE ship_ready IS NULL;

CREATE INDEX IF NOT EXISTS idx_questions_ship_ready ON questions (ship_ready);
