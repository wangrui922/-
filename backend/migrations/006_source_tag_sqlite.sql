-- 006 · questions 表新增 source_tag 列（溯源标记 · T06） · SQLite 方言
--
-- 与 PG 版的差异：SQLite 的 ALTER TABLE ADD COLUMN **不支持** NOT NULL + DEFAULT
-- 同条语法，且不支持 IF NOT EXISTS（重复执行报 "duplicate column name"）。
-- 因此：① 列建为 nullable；② 紧接着在**同一事务**内回填，保证不出现
--   「列已存在但存量题 source_tag 为空」的中间态；③ 生产/开发若担心半吊子状态，
--   可改走 app/services/importers/schema_guard.py::ensure_source_tag_column()
--   （先探测列是否存在，再决定 ALTER + 回填）。

BEGIN TRANSACTION;

ALTER TABLE questions ADD COLUMN source_tag VARCHAR(8);

-- AI 生成归档题标 red（进待抽检队列，本就不进抽题池）
UPDATE questions SET source_tag = 'red' WHERE source_doc = 'ai_gen';
-- 其余(docx 资料题)标 green
UPDATE questions SET source_tag = 'green' WHERE source_tag IS NULL OR source_tag <> 'red';

COMMIT;
