-- =============================================================================
-- AI 数学教练 V4.1 —— 初始建表 DDL (SQLite 兼容版)
-- 类型: CHAR(36) 主键 / TEXT 存 JSON / 时间用 TEXT(ISO8601) / 布尔用 INTEGER
-- 无 uuid 自动生成函数（由 ORM 侧 uuid.uuid4() 填充）
-- 外键需在连接时开启 PRAGMA foreign_keys=ON 才生效
-- =============================================================================

CREATE TABLE users (
    id            CHAR(36) PRIMARY KEY,
    phone         VARCHAR(20) NOT NULL UNIQUE,
    access_pin_hash VARCHAR(255),
    nickname      VARCHAR(50),
    avatar_url    TEXT,
    role          VARCHAR(16) NOT NULL DEFAULT 'student'
                    CHECK (role IN ('student','parent','admin')),
    status        VARCHAR(16) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','frozen')),
    last_login_at TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    deleted_at    TEXT
);
CREATE INDEX idx_users_created_at ON users (created_at);
CREATE INDEX idx_users_role       ON users (role);

CREATE TABLE students (
    id            CHAR(36) PRIMARY KEY,
    user_id       CHAR(36) NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    real_name     VARCHAR(50),
    grade         INTEGER NOT NULL DEFAULT 4 CHECK (grade >= 1 AND grade <= 12),
    stage         VARCHAR(16) NOT NULL DEFAULT 'primary'
                    CHECK (stage IN ('primary','junior','senior')),
    goal          TEXT,
    daily_target  INTEGER NOT NULL DEFAULT 5 CHECK (daily_target > 0 AND daily_target <= 50),
    streak_days   INTEGER NOT NULL DEFAULT 0,
    total_points  INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX idx_students_user_id ON students (user_id);
CREATE INDEX idx_students_grade   ON students (grade);

CREATE TABLE knowledge_points (
    id           CHAR(36) PRIMARY KEY,
    name         VARCHAR(64) NOT NULL,
    parent_id    CHAR(36) REFERENCES knowledge_points(id) ON DELETE SET NULL,
    description  TEXT,
    code         VARCHAR(32) UNIQUE,
    sort         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_knowledge_points_parent ON knowledge_points (parent_id);

CREATE TABLE questions (
    id           CHAR(36) PRIMARY KEY,
    content      TEXT NOT NULL,
    image_url    VARCHAR(512),
    subject      VARCHAR(16) NOT NULL DEFAULT '数学',
    grade        INTEGER,
    difficulty   INTEGER CHECK (difficulty IN (1,3,5)),
    kp_id        CHAR(36) REFERENCES knowledge_points(id) ON DELETE SET NULL,
    type         VARCHAR(16),
    options      TEXT,
    status       VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at   TEXT NOT NULL
);
CREATE INDEX idx_questions_kp ON questions (kp_id);
CREATE INDEX idx_questions_status_grade ON questions (status, grade);

CREATE TABLE answers (
    id                  CHAR(36) PRIMARY KEY,
    question_id         CHAR(36) NOT NULL UNIQUE REFERENCES questions(id) ON DELETE CASCADE,
    correct_answer      TEXT,
    analysis            TEXT,
    distractor_analysis TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE ai_sessions (
    id          CHAR(36) PRIMARY KEY,
    student_id  CHAR(36) NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    question_id CHAR(36) REFERENCES questions(id) ON DELETE SET NULL,
    status      VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_ai_sessions_student ON ai_sessions (student_id);

CREATE TABLE ai_messages (
    id          CHAR(36) PRIMARY KEY,
    session_id  CHAR(36) NOT NULL REFERENCES ai_sessions(id) ON DELETE CASCADE,
    role        VARCHAR(16) NOT NULL CHECK (role IN ('user','assistant','system')),
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_ai_messages_session ON ai_messages (session_id);

CREATE TABLE mistakes (
    id          CHAR(36) PRIMARY KEY,
    student_id  CHAR(36) NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    question_id CHAR(36) NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    error_type  VARCHAR(32),
    error_reason TEXT,
    solved      INTEGER NOT NULL DEFAULT 0,
    solved_at   TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_mistakes_student_solved ON mistakes (student_id, solved);

CREATE TABLE attempts (
    id              CHAR(36) PRIMARY KEY,
    student_id      CHAR(36) NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    question_id     CHAR(36) REFERENCES questions(id) ON DELETE SET NULL,
    session_id      CHAR(36) REFERENCES ai_sessions(id) ON DELETE SET NULL,
    selected_answer TEXT,
    is_correct      INTEGER NOT NULL,
    duration_sec    INTEGER,
    client_event_id VARCHAR(64) UNIQUE,
    created_at      TEXT NOT NULL
);
CREATE INDEX idx_attempts_student_created ON attempts (student_id, created_at);

CREATE TABLE student_profile (
    id             CHAR(36) PRIMARY KEY,
    student_id     CHAR(36) NOT NULL UNIQUE REFERENCES students(id) ON DELETE CASCADE,
    mastery        TEXT NOT NULL DEFAULT '{}',
    weak_points    TEXT NOT NULL DEFAULT '[]',
    learning_style TEXT NOT NULL DEFAULT '{}',
    updated_at     TEXT NOT NULL
);

CREATE TABLE learning_tasks (
    id           CHAR(36) PRIMARY KEY,
    student_id   CHAR(36) NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    task_type    VARCHAR(16) NOT NULL CHECK (task_type IN ('daily','review','exam')),
    items        TEXT NOT NULL,
    status       VARCHAR(16) NOT NULL DEFAULT 'pending',
    generated_at TEXT NOT NULL,
    finished_at  TEXT
);
CREATE INDEX idx_learning_tasks_student ON learning_tasks (student_id);

CREATE TABLE activation_codes (
    id            CHAR(36) PRIMARY KEY,
    code_digest   VARCHAR(64) NOT NULL UNIQUE,
    label         VARCHAR(50),
    user_id       CHAR(36) UNIQUE REFERENCES users(id) ON DELETE SET NULL,
    activated_at  TEXT,
    expires_at    TEXT,
    revoked_at    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX idx_activation_codes_active ON activation_codes (revoked_at, expires_at);
