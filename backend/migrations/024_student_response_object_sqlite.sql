-- 024 · ai_messages 增加学生回答结构化列 response_object（TASK-002 V1）· SQLite 方言
--
-- 与 PG 版的差异（参照既有 023 约定）：
--   * JSONB → TEXT（JSON 序列化由 app/models/base.py::JSONType 负责，
--     裸 SQL 部署时写入方自行保证合法 JSON）；
--   * SQLite 的 ALTER TABLE ADD COLUMN 无 IF NOT EXISTS、无 COMMENT ON——
--     幂等由 sync_schema.py「先检查再执行」或 PRAGMA table_info 预检保证；
--   * 列可空（nullable），无需 server_default——绕开 SQLite
--     「NOT NULL 加列必须带默认值」的限制。
--
-- 幂等说明：重复执行前必须先确认列不存在（PRAGMA table_info(ai_messages)）。
-- 开发/测试库由 Base.metadata.create_all 建出（模型已登记），
-- 本文件用于手动对齐或离线 SQLite 部署；冷母版同步按设计文档 §5.4 六步执行。

BEGIN TRANSACTION;

ALTER TABLE ai_messages ADD COLUMN response_object TEXT;

COMMIT;
