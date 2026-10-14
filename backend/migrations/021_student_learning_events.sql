-- 021 · 学习行为事件表 student_learning_events（DNA-004）· PostgreSQL 方言
--
-- 背景：V6.5 Sprint 2 DNA-004。记录学生学习行为事件流，支撑 DNA-005
-- 「最近事件摘要」与后续学习分析；现有作答流程（practice/chat 回调）
-- 埋点写入本表。
--
-- 设计（与 app/models/dna.py::StudentLearningEvent 逐字对齐，勿漂移）：
--   * event_type 八类枚举硬校验（CHECK）：QUESTIONVIEW/ANSWER/HINT/ERROR/
--     CORRECT/REVIEW/SESSIONSTART/SESSIONEND（大写存储）；
--   * question_id / knowledge_point_id 可空，学生删除不级联（SET NULL）；
--   * meta JSONB 承载事件上下文（时长、session、客户端等）；
--   * idx_sle_student_created 加速「按学生拉最近事件」。
--
-- 幂等说明：CREATE TABLE / INDEX / COMMENT 均带 IF NOT EXISTS，可重复执行。
-- 执行方式：psql -U coach -h 127.0.0.1 -p 5433 -d coach -f backend/migrations/021_student_learning_events.sql
-- SQLite 版本见同目录 021_student_learning_events_sqlite.sql。

BEGIN;

CREATE TABLE IF NOT EXISTS student_learning_events (
    id                  UUID        PRIMARY KEY,
    student_id          UUID        NOT NULL REFERENCES students (id) ON DELETE CASCADE,
    event_type          VARCHAR(24) NOT NULL,
    question_id         UUID        REFERENCES questions (id) ON DELETE SET NULL,
    knowledge_point_id  UUID        REFERENCES knowledge_points (id) ON DELETE SET NULL,
    meta                JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_student_learning_events_type
        CHECK (event_type IN ('QUESTIONVIEW','ANSWER','HINT','ERROR','CORRECT','REVIEW','SESSIONSTART','SESSIONEND'))
);

CREATE INDEX IF NOT EXISTS idx_sle_student_created
    ON student_learning_events (student_id, created_at);

COMMENT ON TABLE  student_learning_events                   IS '学习行为事件表(V6.5 DNA-004)';
COMMENT ON COLUMN student_learning_events.event_type        IS '事件类型 QUESTIONVIEW/ANSWER/HINT/ERROR/CORRECT/REVIEW/SESSIONSTART/SESSIONEND';
COMMENT ON COLUMN student_learning_events.question_id       IS '关联题目(可为空)';
COMMENT ON COLUMN student_learning_events.knowledge_point_id IS '关联知识点(可为空)';
COMMENT ON COLUMN student_learning_events.meta              IS '事件上下文(JSON)';

COMMIT;