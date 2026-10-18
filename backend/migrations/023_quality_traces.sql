-- 023 · 教学行为质量事件流水表 quality_traces（Benchmark 维度一）· PostgreSQL 方言
--
-- 背景：60 人内测开跑前第二梯队开发项「Benchmark 两维评测」。规划要求核心
-- 指标含「AI 直接给答案率」等教学行为维度，但原先 answer_guard 检测到答案
-- 泄漏只写 logger 不累计、讲解质量评分不落库——指标无事实源，无法复盘。
--
-- 设计（与 app/models/quality_trace.py::QualityTrace 逐字对齐，勿漂移）：
--   * student_id 可空：系统级（无学生归属）的教学行为事件也要能记；
--     ON DELETE CASCADE 与其余学生维度表保持一致；
--   * session_id / question_id 归参可空，ON DELETE SET NULL——会话/题目
--     删除不拖累指标流水（与 attempts 的 SET NULL 策略一致）；
--   * event_type 两值：answer_leak（答案泄漏）/ explanation_quality
--     （讲解质量 5 维评分）；severity 三值：info/warning/error；
--   * payload JSONB 承载事件上下文（answer_leak 带 mode/strict/excerpt；
--     explanation_quality 带 5 维分数/overall/grade/threshold/passed 等）；
--   * 只追加不更新：created_at 即事件发生时间（UTC），无 updated_at。
--
-- 幂等说明：CREATE TABLE / INDEX 均带 IF NOT EXISTS，可重复执行。
-- 执行方式：psql -U coach -h 127.0.0.1 -p 5433 -d coach -f backend/migrations/023_quality_traces.sql
-- SQLite 版本见同目录 023_quality_traces_sqlite.sql。

BEGIN;

CREATE TABLE IF NOT EXISTS quality_traces (
    id           UUID        PRIMARY KEY,
    student_id   UUID        REFERENCES students (id) ON DELETE CASCADE,
    session_id   UUID        REFERENCES ai_sessions (id) ON DELETE SET NULL,
    question_id  UUID        REFERENCES questions (id) ON DELETE SET NULL,
    event_type   VARCHAR(32) NOT NULL,
    severity     VARCHAR(16) NOT NULL DEFAULT 'warning',
    payload      JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_quality_traces_event_type CHECK (event_type IN ('answer_leak','explanation_quality')),
    CONSTRAINT ck_quality_traces_severity   CHECK (severity IN ('info','warning','error'))
);

CREATE INDEX IF NOT EXISTS idx_quality_traces_type_created
    ON quality_traces (event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_quality_traces_student_created
    ON quality_traces (student_id, created_at);

COMMENT ON TABLE  quality_traces              IS '教学行为质量事件流水表(Benchmark维度一)';
COMMENT ON COLUMN quality_traces.student_id   IS '所属学生(可空:系统级事件)';
COMMENT ON COLUMN quality_traces.session_id   IS '关联AI会话(可空)';
COMMENT ON COLUMN quality_traces.question_id  IS '关联题目(可空)';
COMMENT ON COLUMN quality_traces.event_type   IS '事件类型 answer_leak/explanation_quality';
COMMENT ON COLUMN quality_traces.severity     IS '严重度 info/warning/error';
COMMENT ON COLUMN quality_traces.payload      IS '事件上下文(JSON):评分分数/泄漏摘录等';
COMMENT ON COLUMN quality_traces.created_at   IS '事件发生时间(UTC)';

COMMIT;
