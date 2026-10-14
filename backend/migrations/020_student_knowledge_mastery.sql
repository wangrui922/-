-- 020 · 学生知识点掌握表 student_knowledge_mastery（DNA-003）· PostgreSQL 方言
--
-- 背景：V6.5 Sprint 2 DNA-003。与 T04 ``student_mastery``（看板物化缓存，
-- mastery_score/computed_at）定位不同：本表是**掌握源数据**，逐知识点记录
-- mastery/confidence + 历史时间戳 + 次数，供 Sprint 3 Mastery 引擎读写。
--
-- 设计（与 app/models/dna.py::StudentKnowledgeMastery 逐字对齐，勿漂移）：
--   * 唯一约束 (student_id, knowledge_point_id)，同学生同知识点只一行；
--   * mastery/confidence 0~1（无 CHECK 上下界，服务层做 [0,1] 归约）；
--   * 五维 concept/procedure/independent/transfer/retention 先建列，
--     Sprint 3（M-005）启用，暂可空；
--   * idx_skm_student_updated 加速「按学生拉最近练习」聚合查询。
--
-- 幂等说明：CREATE TABLE / INDEX / COMMENT 均带 IF NOT EXISTS，可重复执行。
-- 执行方式：psql -U coach -h 127.0.0.1 -p 5433 -d coach -f backend/migrations/020_student_knowledge_mastery.sql
-- SQLite 版本见同目录 020_student_knowledge_mastery_sqlite.sql。

BEGIN;

CREATE TABLE IF NOT EXISTS student_knowledge_mastery (
    id                   UUID        PRIMARY KEY,
    student_id           UUID        NOT NULL REFERENCES students (id) ON DELETE CASCADE,
    knowledge_point_id   UUID        NOT NULL REFERENCES knowledge_points (id) ON DELETE CASCADE,
    mastery              DOUBLE PRECISION NOT NULL DEFAULT 0,
    confidence           DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_practice_at     TIMESTAMPTZ,
    last_correct_at      TIMESTAMPTZ,
    last_wrong_at        TIMESTAMPTZ,
    practice_count       INTEGER     NOT NULL DEFAULT 0,
    correct_count        INTEGER     NOT NULL DEFAULT 0,
    wrong_count          INTEGER     NOT NULL DEFAULT 0,
    -- 五维预留列（Sprint 3 启用，先建列）
    concept_mastery      DOUBLE PRECISION,
    procedure_mastery    DOUBLE PRECISION,
    independent_mastery  DOUBLE PRECISION,
    transfer_mastery     DOUBLE PRECISION,
    retention_mastery    DOUBLE PRECISION,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_skm_student_kp UNIQUE (student_id, knowledge_point_id)
);

CREATE INDEX IF NOT EXISTS idx_skm_student_updated
    ON student_knowledge_mastery (student_id, last_practice_at);

COMMENT ON TABLE  student_knowledge_mastery                        IS '学生知识点掌握表(V6.5 DNA-003)';
COMMENT ON COLUMN student_knowledge_mastery.mastery                IS '整体掌握度 0~1';
COMMENT ON COLUMN student_knowledge_mastery.confidence             IS '置信度 0~1';
COMMENT ON COLUMN student_knowledge_mastery.last_practice_at       IS '最近练习时间(UTC)';
COMMENT ON COLUMN student_knowledge_mastery.last_correct_at        IS '最近答对时间(UTC)';
COMMENT ON COLUMN student_knowledge_mastery.last_wrong_at          IS '最近答错时间(UTC)';
COMMENT ON COLUMN student_knowledge_mastery.practice_count         IS '累计练习次数';
COMMENT ON COLUMN student_knowledge_mastery.correct_count          IS '累计答对次数';
COMMENT ON COLUMN student_knowledge_mastery.wrong_count            IS '累计答错次数';
COMMENT ON COLUMN student_knowledge_mastery.concept_mastery        IS '概念理解维 0~1(Sprint 3 启用)';
COMMENT ON COLUMN student_knowledge_mastery.procedure_mastery      IS '程序执行维 0~1(Sprint 3 启用)';
COMMENT ON COLUMN student_knowledge_mastery.independent_mastery    IS '独立完成维 0~1(Sprint 3 启用)';
COMMENT ON COLUMN student_knowledge_mastery.transfer_mastery       IS '迁移应用维 0~1(Sprint 3 启用)';
COMMENT ON COLUMN student_knowledge_mastery.retention_mastery      IS '保持留存维 0~1(Sprint 3 启用)';

COMMIT;