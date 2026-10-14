-- 012 · 方向六：游戏化学习系统 · SQLite 方言
--
-- 与 PG 版的差异（参照既有约定）：
--   * UUID → CHAR(36)（Python 侧 uuid4() 生成，不用 gen_random_uuid()）
--   * JSONB → TEXT（app/models/base.py::JSONType 负责 json 序列化）
--   * TIMESTAMPTZ → TEXT（ISO 8601 字符串）
--   * 无 COMMENT ON 语法，注释写在行内
--   * ALTER TABLE ADD COLUMN **不支持 IF NOT EXISTS**，重复执行会报
--     "duplicate column name"；生产/开发请优先走
--     app.services.importers.schema_guard.py::ensure_gamification_columns()。
--   * 徽章种子用固定 CHAR(36) 主键 + INSERT OR IGNORE，可重复执行。
--
-- 幂等说明：CREATE TABLE / INDEX 均带 IF NOT EXISTS；徽章种子幂等。
-- 开发/测试库实际由 Base.metadata.create_all 建出（scripts/init_db.py），
-- 本文件用于手动对齐或离线 SQLite 部署。

BEGIN TRANSACTION;

-- ============================================================
-- 1. 游戏会话表
-- ============================================================
CREATE TABLE IF NOT EXISTS game_sessions (
    id                CHAR(36)    PRIMARY KEY,
    student_id        CHAR(36)    NOT NULL REFERENCES students (id) ON DELETE CASCADE,
    game_type         VARCHAR(32) NOT NULL
                        CHECK (game_type IN ('speed_challenge','math_battle')),
    score             INTEGER     NOT NULL DEFAULT 0,
    correct_count     INTEGER     NOT NULL DEFAULT 0,
    wrong_count       INTEGER     NOT NULL DEFAULT 0,
    max_combo         INTEGER     NOT NULL DEFAULT 0,
    duration_sec      INTEGER     NOT NULL DEFAULT 0,
    difficulty_level  INTEGER     NOT NULL DEFAULT 1
                        CHECK (difficulty_level BETWEEN 1 AND 3),
    battle_wave       INTEGER     NOT NULL DEFAULT 0,
    battle_best_wave  INTEGER     NOT NULL DEFAULT 0,
    session_metadata  TEXT,
    created_at        TEXT        NOT NULL,
    ended_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_game_sessions_student_type
    ON game_sessions (student_id, game_type);
CREATE INDEX IF NOT EXISTS idx_game_sessions_created
    ON game_sessions (created_at);

-- ============================================================
-- 2. 学生积分总览表
-- ============================================================
CREATE TABLE IF NOT EXISTS student_points (
    id                CHAR(36)  PRIMARY KEY,
    student_id        CHAR(36)  NOT NULL UNIQUE REFERENCES students (id) ON DELETE CASCADE,
    total_points      INTEGER   NOT NULL DEFAULT 0,
    weekly_points     INTEGER   NOT NULL DEFAULT 0,
    monthly_points    INTEGER   NOT NULL DEFAULT 0,
    battle_best_wave  INTEGER   NOT NULL DEFAULT 0,
    speed_best_score  INTEGER   NOT NULL DEFAULT 0,
    total_games       INTEGER   NOT NULL DEFAULT 0,
    total_correct     INTEGER   NOT NULL DEFAULT 0,
    total_wrong       INTEGER   NOT NULL DEFAULT 0,
    streak_days       INTEGER   NOT NULL DEFAULT 0,
    last_played_at    TEXT,
    updated_at        TEXT      NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_student_points_weekly
    ON student_points (weekly_points);
CREATE INDEX IF NOT EXISTS idx_student_points_monthly
    ON student_points (monthly_points);

-- ============================================================
-- 3. 徽章定义表
-- ============================================================
CREATE TABLE IF NOT EXISTS badges (
    id              CHAR(36)    PRIMARY KEY,
    name            VARCHAR(64) NOT NULL UNIQUE,
    display_name    VARCHAR(128) NOT NULL,
    description     VARCHAR(256) NOT NULL,
    category        VARCHAR(32) NOT NULL
                      CHECK (category IN ('streak','accuracy','speed','explore','battle')),
    rarity          VARCHAR(16) NOT NULL
                      CHECK (rarity IN ('common','rare','epic','legendary')),
    icon_url        VARCHAR(512),
    condition_type  VARCHAR(32) NOT NULL,
    condition_value INTEGER,
    sort_order      INTEGER     NOT NULL DEFAULT 0,
    created_at      TEXT        NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_badges_category ON badges (category);
CREATE INDEX IF NOT EXISTS idx_badges_rarity   ON badges (rarity);

-- ============================================================
-- 4. 学生徽章关联表
-- ============================================================
CREATE TABLE IF NOT EXISTS student_badges (
    id              CHAR(36)  PRIMARY KEY,
    student_id      CHAR(36)  NOT NULL REFERENCES students (id) ON DELETE CASCADE,
    badge_id        CHAR(36)  NOT NULL REFERENCES badges (id)    ON DELETE CASCADE,
    unlocked_at     TEXT      NOT NULL,
    unlock_metadata TEXT,
    CONSTRAINT uq_student_badges UNIQUE (student_id, badge_id)
);

CREATE INDEX IF NOT EXISTS idx_student_badges_student ON student_badges (student_id);
CREATE INDEX IF NOT EXISTS idx_student_badges_badge   ON student_badges (badge_id);

-- ============================================================
-- 5. 排行榜快照表
-- ============================================================
CREATE TABLE IF NOT EXISTS leaderboard (
    id            CHAR(36)   PRIMARY KEY,
    student_id    CHAR(36)   NOT NULL REFERENCES students (id) ON DELETE CASCADE,
    rank_type     VARCHAR(16) NOT NULL
                    CHECK (rank_type IN ('weekly','monthly')),
    rank_period   VARCHAR(16) NOT NULL,
    rank_position INTEGER    NOT NULL,
    score         INTEGER    NOT NULL,
    games_count   INTEGER    NOT NULL DEFAULT 0,
    best_wave     INTEGER    NOT NULL DEFAULT 0,
    updated_at    TEXT       NOT NULL,
    CONSTRAINT uq_leaderboard_period_student UNIQUE (rank_type, rank_period, student_id)
);

CREATE INDEX IF NOT EXISTS idx_leaderboard_period
    ON leaderboard (rank_type, rank_period, rank_position);
CREATE INDEX IF NOT EXISTS idx_leaderboard_student
    ON leaderboard (student_id);

-- ============================================================
-- 6. 扩展 questions 表支持生活场景题库（加列前先探测列是否存在）
-- ============================================================
-- SQLite 官方做法：ALTER TABLE ADD COLUMN 无 IF NOT EXISTS。
-- 本文件面向全新/未加列库（CREATE TABLE 都在上方同一事务）；若存量库已加过列，
-- 重复执行会在下方报 duplicate column —— 此时先核对 PRAGMA table_info(questions)，
-- 已存在则跳过本段或改用 schema_guard.py 幂等入口。
ALTER TABLE questions ADD COLUMN scenario_type     VARCHAR(32);
ALTER TABLE questions ADD COLUMN scenario_template TEXT;
ALTER TABLE questions ADD COLUMN scenario_params   TEXT;

CREATE INDEX IF NOT EXISTS idx_questions_scenario
    ON questions (scenario_type)
    WHERE scenario_type IS NOT NULL;

-- ============================================================
-- 7. 初始化 15 枚徽章（固定主键 + INSERT OR IGNORE 幂等）
-- ============================================================
INSERT OR IGNORE INTO badges (id, name, display_name, description, category, rarity, icon_url, condition_type, condition_value, sort_order) VALUES
('00000000-0000-0000-0000-000000000001', 'streak_novice',      '连胜新手',   '连续答对3题',            'streak',   'common',    '🥉', 'max_streak',     3,   1),
('00000000-0000-0000-0000-000000000002', 'streak_pro',         '连胜高手',   '连续答对10题',           'streak',   'rare',      '🥈', 'max_streak',     10,  2),
('00000000-0000-0000-0000-000000000003', 'streak_master',      '连胜大师',   '连续答对25题',           'streak',   'epic',      '🥇', 'max_streak',     25,  3),
('00000000-0000-0000-0000-000000000004', 'streak_legend',      '连胜传奇',   '连续答对50题',           'streak',   'legendary', '👑', 'max_streak',     50,  4),
('00000000-0000-0000-0000-000000000005', 'accuracy_perfect',   '准星',      '单局准确率100%',         'accuracy', 'common',    '🎯', 'accuracy_perfect', 1,   5),
('00000000-0000-0000-0000-000000000006', 'accuracy_100',       '百发百中',  '累计100题准确率100%',    'accuracy', 'epic',      '🏹', 'accuracy_total', 100,  6),
('00000000-0000-0000-0000-000000000007', 'speed_lightning',    '闪电手',    '单题平均用时<3秒',       'speed',    'rare',      '⚡', 'avg_time_sec',    3,   7),
('00000000-0000-0000-0000-000000000008', 'speed_god',          '神算子',    '单题平均用时<1.5秒',     'speed',    'legendary', '🔥', 'avg_time_sec',    1,   8),
('00000000-0000-0000-0000-000000000009', 'explore_first',      '初次尝试',  '首次完成游戏',           'explore',  'common',    '🎮', 'first_game',      1,   9),
('00000000-0000-0000-0000-000000000010', 'explore_challenger', '挑战者',    '累计完成10局游戏',       'explore',  'rare',      '🏆', 'total_games',    10,  10),
('00000000-0000-0000-0000-000000000011', 'explore_veteran',    '游戏达人',  '累计完成100局游戏',      'explore',  'epic',      '💎', 'total_games',   100,  11),
('00000000-0000-0000-0000-000000000012', 'explore_scenario',   '生活数学家', '首次完成场景题',         'explore',  'rare',      '🌍', 'first_scenario',  1,  12),
('00000000-0000-0000-0000-000000000013', 'battle_wave1',       '初出茅庐',  '战斗模式通关第1波',      'battle',   'common',    '🛡️', 'battle_wave',     1,  13),
('00000000-0000-0000-0000-000000000014', 'battle_wave5',       '数学骑士',  '战斗模式通关第5波',      'battle',   'rare',      '⚔️', 'battle_wave',     5,  14),
('00000000-0000-0000-0000-000000000015', 'battle_wave10',      '屠龙勇士',  '战斗模式通关第10波',     'battle',   'legendary', '🐉', 'battle_wave',    10,  15);

COMMIT;