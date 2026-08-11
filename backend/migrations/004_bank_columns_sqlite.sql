-- 004 · questions 增加题库来源 5 列（T05 · docx 结构化导入） · SQLite 方言
--
-- 与 PG 版的差异（参照 001_init_sqlite.sql / 003_weekly_reports_sqlite.sql 的既有约定）：
--   * SQLite 的 ALTER TABLE ADD COLUMN **不支持 IF NOT EXISTS**，重复执行会报
--     "duplicate column name"；生产/开发请优先走
--     app/services/importers/schema_guard.py::ensure_bank_columns()（先探测列是否存在）。
--   * BOOLEAN 落地为 INTEGER 0/1，默认值写 1 而不是 TRUE。
--   * 无 COMMENT ON 语法，注释写在行内。
--
-- ⚠️ V7 线上事故坑 · 保险①：
--   SQLite 的 DDL 同样支持事务，下面的 ADD COLUMN + UPDATE 回填包在同一个
--   BEGIN/COMMIT 内，保证存量题不会出现「kp_verified=0」的中间态。

BEGIN TRANSACTION;

ALTER TABLE questions ADD COLUMN edition     VARCHAR(16);   -- 青岛版/苏教版
ALTER TABLE questions ADD COLUMN source_type VARCHAR(16);   -- 衔接/期末
ALTER TABLE questions ADD COLUMN section     VARCHAR(32);   -- 温故/预习/提优/摸底/专项
ALTER TABLE questions ADD COLUMN source_doc  VARCHAR(256);  -- 来源 docx 文件名

-- 默认 1(TRUE)：存量题视为已复核；docx 导入器写新题时显式传 0(false)
ALTER TABLE questions ADD COLUMN kp_verified BOOLEAN NOT NULL DEFAULT 1;

-- 回填（与上面的 ADD COLUMN 同事务）：存量题一律标记为已复核
UPDATE questions SET kp_verified = 1 WHERE source_doc IS NULL;

COMMIT;

CREATE INDEX IF NOT EXISTS idx_questions_source_doc     ON questions (source_doc);
CREATE INDEX IF NOT EXISTS idx_questions_edition_grade  ON questions (edition, grade);
