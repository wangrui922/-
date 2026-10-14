-- 020 · 学生知识点掌握表 student_knowledge_mastery（DNA-003）· SQLite 方言
--
-- 与 PG 版的差异（参照既有约定）：
--   * UUID → CHAR(36)；TIMESTAMPTZ → TEXT；无 COMMENT ON
--   * DOUBLE PRECISION → REAL（SQLite 浮点类型）
--   * 表级 CHECK / UNIQUE / INDEX 语法相同
--   * 外键 ON DELETE CASCADE 生效依赖 PRAGMA foreign_keys=ON
--
-- 幂等说明：CREATE TABLE / INDEX 均带 IF NOT EXISTS，可重复执行。
-- 开发/测试库由 Base.metadata.create_all 建出（模型已注册），
-- 本文件用于手动对齐或离线 SQLite 部署。

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS student_knowledge_mastery (
    id                   CHAR(36)    PRIMARY KEY,
    student_id           CHAR(36)    NOT NULL REFERENCES students (id) ON DELETE CASCADE,
    knowledge_point_id   CHAR(36)    NOT NULL REFERENCES knowledge_points (id) ON DELETE CASCADE,
    mastery              REAL        NOT NULL DEFAULT 0,
    confidence           REAL        NOT NULL DEFAULT 0,
    last_practice_at     TEXT,
    last_correct_at      TEXT,
    last_wrong_at        TEXT,
    practice_count       INTEGER     NOT NULL DEFAULT 0,
    correct_count        INTEGER     NOT NULL DEFAULT 0,
    wrong_count          INTEGER     NOT NULL DEFAULT 0,
    -- 五维预留列（Sprint 3 启用，先建列）
    concept_mastery      REAL,
    procedure_mastery    REAL,
    independent_mastery  REAL,
    transfer_mastery     REAL,
    retention_mastery    REAL,
    created_at           TEXT        NOT NULL,
    updated_at           TEXT        NOT NULL,
    CONSTRAINT uq_skm_student_kp UNIQUE (student_id, knowledge_point_id)
);

CREATE INDEX IF NOT EXISTS idx_skm_student_updated
    ON student_knowledge_mastery (student_id, last_practice_at);

COMMIT;