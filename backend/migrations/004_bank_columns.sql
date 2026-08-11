-- 004 · questions 增加题库来源 5 列（T05 · docx 结构化导入） · PostgreSQL 方言
--
-- 新增列：
--   edition      版本（青岛版 / 苏教版）
--   source_type  来源类型（衔接 / 期末）
--   section      册内分区（温故 / 预习 / 提优 / 摸底 / 专项 …）
--   source_doc   来源 docx 文件名（不含路径，便于批量上架/回滚）
--   kp_verified  知识点是否人工复核通过
--
-- ⚠️ V7 线上事故坑 · 保险①：
--   「ADD COLUMN kp_verified」与「UPDATE ... WHERE source_doc IS NULL」
--   必须在**同一个事务**里完成。否则存量 909 题会短暂处于 kp_verified=false，
--   期间 profile_service 的知识点归因会被整体置空 → 掌握度/画像/家长周报当场断流。
--   保险② 在导入器启动时（app/services/importers/schema_guard.py::ensure_bank_columns）
--   做二次探测与回填，并在检测到「source_doc IS NULL 且 kp_verified=false」时拒绝导入。
--
-- 幂等说明：全部语句带 IF NOT EXISTS，可重复执行。

BEGIN;

ALTER TABLE questions ADD COLUMN IF NOT EXISTS edition     VARCHAR(16);
ALTER TABLE questions ADD COLUMN IF NOT EXISTS source_type VARCHAR(16);
ALTER TABLE questions ADD COLUMN IF NOT EXISTS section     VARCHAR(32);
ALTER TABLE questions ADD COLUMN IF NOT EXISTS source_doc  VARCHAR(256);

-- 默认 TRUE：存量题（人工整理的 seed 题库）视为已复核，不影响既有统计口径。
-- docx 导入器写入新题时会**显式**传 kp_verified=false。
ALTER TABLE questions ADD COLUMN IF NOT EXISTS kp_verified BOOLEAN NOT NULL DEFAULT TRUE;

-- 回填（与上面的 ADD COLUMN 同事务）：存量题一律标记为已复核。
UPDATE questions SET kp_verified = TRUE WHERE source_doc IS NULL;

COMMIT;

-- 批量上架 / 抽检的查询路径
CREATE INDEX IF NOT EXISTS idx_questions_source_doc ON questions (source_doc);
-- 按版本 + 年级选题（青岛版学生只练青岛版对齐题）
CREATE INDEX IF NOT EXISTS idx_questions_edition_grade ON questions (edition, grade);

COMMENT ON COLUMN questions.edition     IS '教材版本：青岛版/苏教版';
COMMENT ON COLUMN questions.source_type IS '来源类型：衔接/期末';
COMMENT ON COLUMN questions.section     IS '册内分区：温故/预习/提优/摸底/专项';
COMMENT ON COLUMN questions.source_doc  IS '来源 docx 文件名（不含路径）';
COMMENT ON COLUMN questions.kp_verified IS '知识点是否人工复核通过；false 的题不参与知识点归因';
