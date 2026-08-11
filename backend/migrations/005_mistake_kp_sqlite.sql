-- 005 · mistakes 表新增 kp_id 列（错题→知识点深链） · SQLite 方言
--
-- 与 PG 版的差异（参照 004_bank_columns_sqlite.sql 的既有约定）：
--   * SQLite 的 ALTER TABLE ADD COLUMN **不支持 IF NOT EXISTS**，重复执行会报
--     "duplicate column name"；生产/开发请优先走
--     app/services/importers/schema_guard.py::ensure_mistake_kp_column()（先探测列是否存在）。
--   * CHAR(36) 替代 UUID（与 base.GUID 的 SQLite 落地一致）。
--   * 无 COMMENT ON 语法，注释写在行内。
--
-- 事务说明：SQLite 的 DDL 同样支持事务，ADD COLUMN + UPDATE 回填包在同一个
-- BEGIN/COMMIT 内，保证存量错题不会出现 kp_id 为空但表结构已变更的中间态。

BEGIN TRANSACTION;

-- 新增 kp_id 列：FK→knowledge_points.id，ON DELETE SET NULL，nullable
ALTER TABLE mistakes ADD COLUMN kp_id CHAR(36) REFERENCES knowledge_points(id) ON DELETE SET NULL;

-- 回填（与 ADD COLUMN 同事务）：存量错题从题目继承 kp_id
UPDATE mistakes SET kp_id = (
    SELECT q.kp_id FROM questions q WHERE q.id = mistakes.question_id
) WHERE mistakes.kp_id IS NULL;

COMMIT;

-- 按知识点筛错题的查询路径
CREATE INDEX IF NOT EXISTS idx_mistakes_kp ON mistakes (kp_id);
