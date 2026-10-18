-- 023 · 教学行为质量事件流水表 quality_traces（Benchmark 维度一）· SQLite 方言
--
-- 与 PG 版的差异（参照既有约定）：
--   * UUID → CHAR(36)；TIMESTAMPTZ → TEXT；JSONB → TEXT（JSON 序列化由
--     app/models/base.py::JSONType 负责，裸 SQL 部署时写入方自行保证合法 JSON）；
--   * 无 COMMENT ON；表级 CHECK / INDEX 语法相同；
--   * 外键 ON DELETE CASCADE / SET NULL 生效依赖 PRAGMA foreign_keys=ON
--     （app/db.py 连接时已统一开启）。
--
-- 幂等说明：CREATE TABLE / INDEX 均带 IF NOT EXISTS，可重复执行。
-- 开发/测试库由 Base.metadata.create_all 建出（模型已登记），
-- 本文件用于手动对齐或离线 SQLite 部署。

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS quality_traces (
    id           CHAR(36)     PRIMARY KEY,
    student_id   CHAR(36)     REFERENCES students (id) ON DELETE CASCADE,
    session_id   CHAR(36)     REFERENCES ai_sessions (id) ON DELETE SET NULL,
    question_id  CHAR(36)     REFERENCES questions (id) ON DELETE SET NULL,
    event_type   VARCHAR(32)  NOT NULL,
    severity     VARCHAR(16)  NOT NULL DEFAULT 'warning',
    payload      TEXT,
    created_at   TEXT         NOT NULL,
    CONSTRAINT ck_quality_traces_event_type CHECK (event_type IN ('answer_leak','explanation_quality')),
    CONSTRAINT ck_quality_traces_severity   CHECK (severity IN ('info','warning','error'))
);

CREATE INDEX IF NOT EXISTS idx_quality_traces_type_created
    ON quality_traces (event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_quality_traces_student_created
    ON quality_traces (student_id, created_at);

COMMIT;
