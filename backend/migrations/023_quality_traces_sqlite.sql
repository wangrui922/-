-- 023 · 教学行为质量事件流水表 quality_traces（Benchmark 维度一）· SQLite 方言
--
-- 生成方式（重要）：本文件 DDL 由 ``CreateTable(QualityTrace.__table__)`` 按
-- sqlite dialect 编译生成后手工排版——**模型是唯一真相源**，迁移文件必须与
-- ``Base.metadata.create_all`` 的产出逐字一致（021 先例）。旧手写版
-- 曾漂移过三处（id 缺 NOT NULL、severity 缺 DEFAULT、created_at 用 TEXT 且无
-- DEFAULT、缺 3 个 FK 自动索引），导致母版与 dev.db 指纹不符；2026-10-18 已
-- 按模型重建收敛，并给 content_fingerprint 加了列结构哈希防复发。
--
-- 与 PG 版的差异（方言转换由 SQLAlchemy 编译保证）：
--   * UUID → CHAR(36)；TIMESTAMPTZ → DATETIME；JSONB → TEXT（JSON 序列化由
--     app/models/base.py::JSONType 负责，裸 SQL 部署时写入方自行保证合法 JSON）；
--   * 无 COMMENT ON；表级 CHECK / FOREIGN KEY / INDEX 语法相同；
--   * 外键 ON DELETE CASCADE / SET NULL 生效依赖 PRAGMA foreign_keys=ON
--     （app/db.py 连接时已统一开启）。
--
-- 幂等说明：CREATE TABLE / INDEX 均带 IF NOT EXISTS，可重复执行。
-- 开发/测试库由 Base.metadata.create_all 建出（模型已登记），
-- 本文件用于手动对齐或离线 SQLite 部署；冷母版同步按「先备份再执行」路径。

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS quality_traces (
    id           CHAR(36)     NOT NULL,
    student_id   CHAR(36),
    session_id   CHAR(36),
    question_id  CHAR(36),
    event_type   VARCHAR(32)  NOT NULL,
    severity     VARCHAR(16)  DEFAULT 'warning' NOT NULL,
    payload      TEXT,
    created_at   DATETIME     DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_quality_traces_event_type CHECK (event_type IN ('answer_leak','explanation_quality')),
    CONSTRAINT ck_quality_traces_severity   CHECK (severity IN ('info','warning','error')),
    FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE CASCADE,
    FOREIGN KEY(session_id) REFERENCES ai_sessions (id) ON DELETE SET NULL,
    FOREIGN KEY(question_id) REFERENCES questions (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_quality_traces_type_created
    ON quality_traces (event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_quality_traces_student_created
    ON quality_traces (student_id, created_at);
CREATE INDEX IF NOT EXISTS ix_quality_traces_student_id
    ON quality_traces (student_id);
CREATE INDEX IF NOT EXISTS ix_quality_traces_session_id
    ON quality_traces (session_id);
CREATE INDEX IF NOT EXISTS ix_quality_traces_question_id
    ON quality_traces (question_id);

COMMIT;
