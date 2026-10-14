-- SQLite cannot alter CHECK constraints in place, so rebuild only students.
PRAGMA foreign_keys = OFF;
BEGIN TRANSACTION;

CREATE TABLE students_grade_1_12 (
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

INSERT INTO students_grade_1_12 (
    id, user_id, real_name, grade, stage, goal, daily_target,
    streak_days, total_points, created_at, updated_at
)
SELECT
    id, user_id, real_name, grade,
    CASE
        WHEN grade <= 6 THEN 'primary'
        WHEN grade <= 9 THEN 'junior'
        ELSE 'senior'
    END,
    goal, daily_target, streak_days, total_points, created_at, updated_at
FROM students;

DROP TABLE students;
ALTER TABLE students_grade_1_12 RENAME TO students;
CREATE INDEX idx_students_user_id ON students (user_id);
CREATE INDEX idx_students_grade ON students (grade);

COMMIT;
PRAGMA foreign_keys = ON;
