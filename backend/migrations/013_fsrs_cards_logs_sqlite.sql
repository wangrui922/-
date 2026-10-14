-- 013 · 补建 FSRS review_cards / review_logs 表（SQLite 方言）
-- 幂等（IF NOT EXISTS），对已有库无副作用。
-- 表结构由 review_service.py raw SQL 反推，与 fsrs_scheduler.Card / ReviewLog 对齐。

-- ------------------------------------------------------------------ review_cards
CREATE TABLE IF NOT EXISTS review_cards (
    id               CHAR(36) PRIMARY KEY,
    student_id       CHAR(36) NOT NULL,
    question_id      CHAR(36),
    kp_id            CHAR(36),
    due              TEXT,
    stability        REAL NOT NULL DEFAULT 0,
    difficulty       REAL NOT NULL DEFAULT 0,
    elapsed_days     INTEGER NOT NULL DEFAULT 0,
    scheduled_days   INTEGER NOT NULL DEFAULT 0,
    reps             INTEGER NOT NULL DEFAULT 0,
    lapses           INTEGER NOT NULL DEFAULT 0,
    state            INTEGER NOT NULL DEFAULT 0,
    last_review      TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_review_cards_student ON review_cards (student_id);
CREATE INDEX IF NOT EXISTS idx_review_cards_due     ON review_cards (student_id, due);
CREATE INDEX IF NOT EXISTS idx_review_cards_q       ON review_cards (question_id);
CREATE INDEX IF NOT EXISTS idx_review_cards_kp      ON review_cards (kp_id);

-- ------------------------------------------------------------------ review_logs
CREATE TABLE IF NOT EXISTS review_logs (
    id                CHAR(36) PRIMARY KEY,
    card_id           CHAR(36) NOT NULL,
    student_id        CHAR(36) NOT NULL,
    rating            INTEGER NOT NULL,
    state             INTEGER NOT NULL,
    elapsed_days      INTEGER NOT NULL DEFAULT 0,
    scheduled_days    INTEGER NOT NULL DEFAULT 0,
    stability_before  REAL,
    stability_after   REAL,
    difficulty_before REAL,
    difficulty_after  REAL,
    review_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_review_logs_card     ON review_logs (card_id);
CREATE INDEX IF NOT EXISTS idx_review_logs_student  ON review_logs (student_id);
CREATE INDEX IF NOT EXISTS idx_review_logs_at       ON review_logs (review_at);
