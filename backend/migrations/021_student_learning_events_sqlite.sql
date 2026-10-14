-- 021 · 学习行为事件表 student_learning_events（DNA-004）· SQLite 方言
--
-- 与 PG 版的差异（参照既有约定）：
--   * UUID → CHAR(36)；TIMESTAMPTZ → TEXT；JSONB → TEXT；无 COMMENT ON
--   * 表级 CHECK / INDEX 语法相同
--   * 外键 ON DELETE SET NULL / CASCADE 生效依赖 PRAGMA foreign_keys=ON
--
-- 幂等说明：CREATE TABLE / INDEX 均带 IF NOT EXISTS，可重复执行。
-- 开发/测试库由 Base.metadata.create_all 建出（模型已注册），
-- 本文件用于手动对齐或离线 SQLite 部署。

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS student_learning_events (
    id                  CHAR(36)    PRIMARY KEY,
    student_id          CHAR(36)    NOT NULL REFERENCES students (id) ON DELETE CASCADE,
    event_type          VARCHAR(24) NOT NULL,
    question_id         CHAR(36)    REFERENCES questions (id) ON DELETE SET NULL,
    knowledge_point_id  CHAR(36)    REFERENCES knowledge_points (id) ON DELETE SET NULL,
    meta                TEXT,
    created_at          TEXT        NOT NULL,
    CONSTRAINT ck_student_learning_events_type
        CHECK (event_type IN ('QUESTIONVIEW','ANSWER','HINT','ERROR','CORRECT','REVIEW','SESSIONSTART','SESSIONEND'))
);

CREATE INDEX IF NOT EXISTS idx_sle_student_created
    ON student_learning_events (student_id, created_at);

COMMIT;