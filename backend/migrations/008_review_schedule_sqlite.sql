-- 008 · 新增 review_schedule 表（复习排程 / SM-2 间隔重复 · #65） · SQLite 方言
--
-- 与 PG 版差异：UUID → CHAR(36)，TIMESTAMPTZ → TEXT(ISO8601)，DOUBLE PRECISION → REAL。
-- 开发/测试库实际由 app.db::Base.metadata.create_all 建出（sync_schema.py），
-- 本文件用于手动对齐或离线 SQLite 部署。新建表，幂等。

CREATE TABLE IF NOT EXISTS review_schedule (
    id                   CHAR(36) PRIMARY KEY,
    student_id           CHAR(36) NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    knowledge_point_id   CHAR(36) NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
    ease_factor          REAL NOT NULL DEFAULT 2.5,
    interval_days        INTEGER NOT NULL DEFAULT 0,
    repetitions          INTEGER NOT NULL DEFAULT 0,
    last_reviewed_at     TEXT,
    due_at               TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    CONSTRAINT uq_review_schedule_student_kp UNIQUE (student_id, knowledge_point_id)
);

CREATE INDEX IF NOT EXISTS idx_review_schedule_student ON review_schedule (student_id);
CREATE INDEX IF NOT EXISTS idx_review_schedule_due     ON review_schedule (student_id, due_at);
