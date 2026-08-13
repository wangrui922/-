-- 007 · questions 表新增 ship_ready 列（版权护栏 · T07） · SQLite 方言
--
-- 与 PG 版的差异：SQLite 的 ALTER TABLE ADD COLUMN 为稳妥起见建为 nullable，
-- 再在同事务内回填为 1（可发布），保证不出现「列已存在但存量题 ship_ready 为空」
-- 的中间态。生产/开发若担心半吊子状态，可改走
-- app/services/importers/schema_guard.py::ensure_ship_ready_column()
-- （先探测列是否存在，再决定 ALTER + 回填）。

BEGIN TRANSACTION;

ALTER TABLE questions ADD COLUMN ship_ready BOOLEAN;

-- 存量题（导入本功能前的所有题）默认可发布
UPDATE questions SET ship_ready = 1 WHERE ship_ready IS NULL;

COMMIT;
