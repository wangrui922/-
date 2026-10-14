-- 019 · 学生画像表 student_learning_profiles（DNA-001/DNA-002）· PostgreSQL 方言
--
-- 背景：V6.5 Sprint 2 DNA-001 决策点结论——现有 ``student_profile``
-- （T02）只承载 mastery/weak_points/learning_style 三个 JSON 摘要（给 AI
-- 提示词看），无法承载 DNA-002 需要「非法枚举拒绝」校验的结构化画像字段，
-- 故**新建独立表**（1:1 student_id，不扩旧表）。
--
-- 设计（与 app/models/dna.py::StudentLearningProfile 逐字对齐，勿漂移）：
--   * student_id UNIQUE → 1:1 学生，ON DELETE CASCADE；
--   * learning_goal 八类枚举硬校验（CHECK，非法值在 DB 层也拒绝）；
--   * 其余画像字段：learning_stage/current_level/hint_dependency 为建议枚举
--     String；learning_habits/common_errors/learning_trend 为 JSONB 自由结构；
--   * created_at/updated_at 统一 UTC。
--
-- 幂等说明：CREATE TABLE / INDEX / COMMENT 均带 IF NOT EXISTS，可重复执行。
-- 执行方式：psql -U coach -h 127.0.0.1 -p 5433 -d coach -f backend/migrations/019_student_learning_profiles.sql
-- SQLite 版本见同目录 019_student_learning_profiles_sqlite.sql。

BEGIN;

CREATE TABLE IF NOT EXISTS student_learning_profiles (
    id              UUID         PRIMARY KEY,
    student_id      UUID         NOT NULL UNIQUE REFERENCES students (id) ON DELETE CASCADE,
    learning_goal   VARCHAR(32)  NOT NULL,
    learning_stage  VARCHAR(32),
    current_level   VARCHAR(32),
    learning_habits JSONB,
    common_errors   JSONB,
    hint_dependency VARCHAR(16),
    learning_trend  JSONB,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_student_learning_profiles_goal
        CHECK (learning_goal IN ('提高校内成绩','跟上进度','补基础','期中','期末','中考','高考','自主提高'))
);

COMMENT ON TABLE  student_learning_profiles                    IS '学生画像表(V6.5 DNA-001,1:1)';
COMMENT ON COLUMN student_learning_profiles.student_id         IS '所属学生(1:1)';
COMMENT ON COLUMN student_learning_profiles.learning_goal      IS '学习目标(八类枚举，硬校验)';
COMMENT ON COLUMN student_learning_profiles.learning_stage     IS '学习阶段(建议枚举)';
COMMENT ON COLUMN student_learning_profiles.current_level      IS '当前水平(建议枚举)';
COMMENT ON COLUMN student_learning_profiles.learning_habits    IS '学习习惯(JSON 自由结构)';
COMMENT ON COLUMN student_learning_profiles.common_errors      IS '常见错误(JSON 自由结构)';
COMMENT ON COLUMN student_learning_profiles.hint_dependency    IS '提示依赖 high/medium/low(建议枚举)';
COMMENT ON COLUMN student_learning_profiles.learning_trend     IS '学习趋势(JSON 自由结构)';

COMMIT;