-- 008 · 新增 review_schedule 表（复习排程 / SM-2 间隔重复 · #65） · PostgreSQL 方言
--
-- 与 student_mastery（掌握度）互补：
--   * student_mastery 回答「现在掌握得怎么样」；
--   * review_schedule 回答「下一次什么时候该复习这道知识点」。
-- 状态字段直接映射 app.services.review_scheduler.ReviewState。
-- 新建表，幂等（IF NOT EXISTS），对已有库无副作用。

CREATE TABLE IF NOT EXISTS review_schedule (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id           UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    knowledge_point_id   UUID NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
    ease_factor          DOUBLE PRECISION NOT NULL DEFAULT 2.5,
    interval_days        INTEGER NOT NULL DEFAULT 0,
    repetitions          INTEGER NOT NULL DEFAULT 0,
    last_reviewed_at     TIMESTAMPTZ,
    due_at               TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_review_schedule_student_kp UNIQUE (student_id, knowledge_point_id)
);

CREATE INDEX IF NOT EXISTS idx_review_schedule_student      ON review_schedule (student_id);
CREATE INDEX IF NOT EXISTS idx_review_schedule_due          ON review_schedule (student_id, due_at);
