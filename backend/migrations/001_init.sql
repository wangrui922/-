-- =============================================================================
-- AI 数学教练 V4.1 —— 初始建表 DDL (PostgreSQL)
-- 类型: UUID / JSONB / TEXT / TIMESTAMPTZ
-- 主键默认 gen_random_uuid()(PG >= 13 为内置函数)
-- 外键级联: 学生相关子表 CASCADE；可空外键(题目/会话) SET NULL
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- 兼容老版本 gen_random_uuid()

-- ----------------------------------------------------------------- 账号域
CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone         VARCHAR(20) NOT NULL UNIQUE,
    access_pin_hash VARCHAR(255),
    nickname      VARCHAR(50),
    avatar_url    TEXT,
    role          VARCHAR(16) NOT NULL DEFAULT 'student'
                    CHECK (role IN ('student','parent','admin')),
    status        VARCHAR(16) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','frozen')),
    last_login_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
CREATE INDEX idx_users_created_at ON users (created_at);
CREATE INDEX idx_users_role       ON users (role);

CREATE TABLE students (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    real_name     VARCHAR(50),
    grade         SMALLINT NOT NULL DEFAULT 4 CHECK (grade >= 1 AND grade <= 12),
    stage         VARCHAR(16) NOT NULL DEFAULT 'primary'
                    CHECK (stage IN ('primary','junior','senior')),
    goal          TEXT,
    daily_target  SMALLINT NOT NULL DEFAULT 5 CHECK (daily_target > 0 AND daily_target <= 50),
    streak_days   INTEGER NOT NULL DEFAULT 0,
    total_points  INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_students_user_id ON students (user_id);
CREATE INDEX idx_students_grade   ON students (grade);

-- ----------------------------------------------------------------- 内容域
CREATE TABLE knowledge_points (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         VARCHAR(64) NOT NULL,
    parent_id    UUID REFERENCES knowledge_points(id) ON DELETE SET NULL,
    description  TEXT,
    code         VARCHAR(32) UNIQUE,
    sort         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_knowledge_points_parent ON knowledge_points (parent_id);

CREATE TABLE questions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content      TEXT NOT NULL,
    image_url    VARCHAR(512),
    subject      VARCHAR(16) NOT NULL DEFAULT '数学',
    grade        SMALLINT,
    difficulty   SMALLINT CHECK (difficulty IN (1,3,5)),
    kp_id        UUID REFERENCES knowledge_points(id) ON DELETE SET NULL,
    type         VARCHAR(16),
    options      JSONB,
    status       VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_questions_kp ON questions (kp_id);
CREATE INDEX idx_questions_status_grade ON questions (status, grade);

CREATE TABLE answers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id         UUID NOT NULL UNIQUE REFERENCES questions(id) ON DELETE CASCADE,
    correct_answer      TEXT,
    analysis            TEXT,
    distractor_analysis JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- ----------------------------------------------------------------- AI 域（先于 attempts, 因其被引用）
CREATE TABLE ai_sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id  UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    question_id UUID REFERENCES questions(id) ON DELETE SET NULL,
    status      VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_ai_sessions_student ON ai_sessions (student_id);

CREATE TABLE ai_messages (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  UUID NOT NULL REFERENCES ai_sessions(id) ON DELETE CASCADE,
    role        VARCHAR(16) NOT NULL CHECK (role IN ('user','assistant','system')),
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_ai_messages_session ON ai_messages (session_id);

-- ----------------------------------------------------------------- 学习域
CREATE TABLE mistakes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id  UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    question_id UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    error_type  VARCHAR(32),
    error_reason TEXT,
    solved      BOOLEAN NOT NULL DEFAULT false,
    solved_at   TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_mistakes_student_solved ON mistakes (student_id, solved);

CREATE TABLE attempts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id      UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    question_id     UUID REFERENCES questions(id) ON DELETE SET NULL,
    session_id      UUID REFERENCES ai_sessions(id) ON DELETE SET NULL,
    selected_answer TEXT,
    is_correct      BOOLEAN NOT NULL,
    duration_sec    INTEGER,
    client_event_id VARCHAR(64) UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_attempts_student_created ON attempts (student_id, created_at);

CREATE TABLE student_profile (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id     UUID NOT NULL UNIQUE REFERENCES students(id) ON DELETE CASCADE,
    mastery        JSONB NOT NULL DEFAULT '{}'::jsonb,
    weak_points    JSONB NOT NULL DEFAULT '[]'::jsonb,
    learning_style JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE learning_tasks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id   UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    task_type    VARCHAR(16) NOT NULL CHECK (task_type IN ('daily','review','exam')),
    items        JSONB NOT NULL,
    status       VARCHAR(16) NOT NULL DEFAULT 'pending',
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);
CREATE INDEX idx_learning_tasks_student ON learning_tasks (student_id);

CREATE TABLE activation_codes (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code_digest   VARCHAR(64) NOT NULL UNIQUE,
    label         VARCHAR(50),
    user_id       UUID UNIQUE REFERENCES users(id) ON DELETE SET NULL,
    activated_at  TIMESTAMPTZ,
    expires_at    TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_activation_codes_active ON activation_codes (revoked_at, expires_at);
