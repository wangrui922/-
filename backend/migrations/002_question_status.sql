-- 002 · questions 增加 status 列（T02 · 2.3 seed 需要：初中 16 题入库置 archived）
-- 两方言通用语法：ALTER TABLE ... ADD COLUMN（PG 与 SQLite 均支持）。
-- 幂等说明：SQLite 不支持 IF NOT EXISTS，重复执行会报错；
--           生产请走 backend/scripts/seed_db.py 的 ensure_schema()（先探测列是否存在）。

ALTER TABLE questions ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'active';

CREATE INDEX idx_questions_status_grade ON questions (status, grade);
