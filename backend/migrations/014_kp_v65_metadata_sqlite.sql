-- 014 · knowledge_points 增加 V6.5 学习元数据 7 列 + 时间戳（KG-001） · SQLite 方言
--
-- 与 PG 版的差异（参照 004/005 的既有约定）：
--   * SQLite 的 ALTER TABLE ADD COLUMN **不支持 IF NOT EXISTS**，重复执行会报
--     "duplicate column name"；生产/开发请优先走
--     app/services/importers/schema_guard.py::ensure_knowledge_point_metadata()
--     （先探测列是否存在，缺才 ALTER）。
--   * 无 COMMENT ON 语法，注释写在行内。
--
-- 回填说明：存量行只回填学科/难度/重要程度/状态的保守默认值；
--           年级/学期/章节的细分标注由 seed_kp_v65.py 按 code 逐一补齐。

BEGIN TRANSACTION;

ALTER TABLE knowledge_points ADD COLUMN subject    VARCHAR(16)  NOT NULL DEFAULT 'math';  -- 学科(math)
ALTER TABLE knowledge_points ADD COLUMN grade      SMALLINT;                              -- 适用年级(小学3=3 … 初三=9)
ALTER TABLE knowledge_points ADD COLUMN semester   VARCHAR(8);                            -- 学期(上/下)
ALTER TABLE knowledge_points ADD COLUMN chapter    VARCHAR(64);                           -- 所属章节
ALTER TABLE knowledge_points ADD COLUMN difficulty SMALLINT     NOT NULL DEFAULT 3;       -- 难度档位(1/3/5)
ALTER TABLE knowledge_points ADD COLUMN importance SMALLINT     NOT NULL DEFAULT 3;       -- 重要程度(1-5)
ALTER TABLE knowledge_points ADD COLUMN status     VARCHAR(16)  NOT NULL DEFAULT 'active';-- 状态(active/archived)
ALTER TABLE knowledge_points ADD COLUMN created_at DATETIME; -- 创建时间(UTC)，ADD COLUMN 不支持非恒定默认，回填段补 datetime('now')
ALTER TABLE knowledge_points ADD COLUMN updated_at DATETIME; -- 更新时间(UTC)

-- 回填（与 ADD COLUMN 同事务）：存量知识点视为 math / active / 难度3 / 重要3
UPDATE knowledge_points SET subject = 'math' WHERE subject IS NULL OR subject = '';
UPDATE knowledge_points SET status = 'active' WHERE status IS NULL OR status = '';
-- SQLite 时间戳列此项补值（PG 版由列默认 now() 承担，SQLite 版在此回填）
UPDATE knowledge_points SET created_at = datetime('now'), updated_at = datetime('now') WHERE created_at IS NULL;

COMMIT;