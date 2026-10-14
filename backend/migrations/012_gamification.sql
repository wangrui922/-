-- 012 · 方向六：游戏化学习系统 · PostgreSQL 方言
--
-- 背景：为提升低龄段留存新增 5 张表 + questions 场景题扩展 + 15 枚徽章初始化。
--   详见 docs/direction_six_gamification_design.md（§4 数据库设计）。
--
-- 幂等说明：建表/加列/索引全部带 IF NOT EXISTS；徽章种子用固定 UUID 主键 +
--   ON CONFLICT (id) DO NOTHING，可重复执行。
-- 执行方式：psql -U coach -h 127.0.0.1 -p 5433 -d coach -f backend/migrations/012_gamification.sql
-- SQLite 版本见同目录 012_gamification_sqlite.sql。

BEGIN;

-- ============================================================
-- 1. 游戏会话表（限时挑战 / 回合战斗）
-- ============================================================
CREATE TABLE IF NOT EXISTS game_sessions (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id        UUID         NOT NULL REFERENCES students (id) ON DELETE CASCADE,
    game_type         VARCHAR(32)  NOT NULL,
    score             INTEGER      NOT NULL DEFAULT 0,
    correct_count     INTEGER      NOT NULL DEFAULT 0,
    wrong_count       INTEGER      NOT NULL DEFAULT 0,
    max_combo         INTEGER      NOT NULL DEFAULT 0,
    duration_sec      INTEGER      NOT NULL DEFAULT 0,
    difficulty_level  SMALLINT     NOT NULL DEFAULT 1,
    battle_wave       INTEGER      NOT NULL DEFAULT 0,
    battle_best_wave  INTEGER      NOT NULL DEFAULT 0,
    session_metadata  JSONB,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    ended_at          TIMESTAMPTZ,
    CONSTRAINT ck_game_sessions_type       CHECK (game_type IN ('speed_challenge','math_battle')),
    CONSTRAINT ck_game_sessions_difficulty CHECK (difficulty_level BETWEEN 1 AND 3)
);

COMMENT ON TABLE  game_sessions                    IS '游戏会话记录表(方向六)';
COMMENT ON COLUMN game_sessions.session_metadata   IS 'JSON：题目列表、答题记录、怪物配置快照';
COMMENT ON COLUMN game_sessions.battle_wave        IS '战斗模式当前波次(0=非战斗)';
COMMENT ON COLUMN game_sessions.battle_best_wave   IS '战斗模式历史最高波次';

CREATE INDEX IF NOT EXISTS idx_game_sessions_student_type
    ON game_sessions (student_id, game_type);
CREATE INDEX IF NOT EXISTS idx_game_sessions_created
    ON game_sessions (created_at);

-- ============================================================
-- 2. 学生积分总览表（1:1 对应 students）
-- ============================================================
CREATE TABLE IF NOT EXISTS student_points (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id       UUID        NOT NULL UNIQUE REFERENCES students (id) ON DELETE CASCADE,
    total_points     INTEGER     NOT NULL DEFAULT 0,
    weekly_points    INTEGER     NOT NULL DEFAULT 0,
    monthly_points   INTEGER     NOT NULL DEFAULT 0,
    battle_best_wave INTEGER     NOT NULL DEFAULT 0,
    speed_best_score INTEGER     NOT NULL DEFAULT 0,
    total_games      INTEGER     NOT NULL DEFAULT 0,
    total_correct    INTEGER     NOT NULL DEFAULT 0,
    total_wrong      INTEGER     NOT NULL DEFAULT 0,
    streak_days      INTEGER     NOT NULL DEFAULT 0,
    last_played_at   TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  student_points               IS '学生积分总览表(1:1, 游戏数据聚合, 方向六)';
COMMENT ON COLUMN student_points.weekly_points IS '本周积分(周日00:00由定时任务清零/归档)';
COMMENT ON COLUMN student_points.monthly_points IS '本月积分(每月1日00:00清零/归档)';

CREATE INDEX IF NOT EXISTS idx_student_points_weekly
    ON student_points (weekly_points);
CREATE INDEX IF NOT EXISTS idx_student_points_monthly
    ON student_points (monthly_points);

-- ============================================================
-- 3. 徽章定义表（全局配置，初始化后只读）
-- ============================================================
CREATE TABLE IF NOT EXISTS badges (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(64)  NOT NULL UNIQUE,
    display_name    VARCHAR(128) NOT NULL,
    description     VARCHAR(256) NOT NULL,
    category        VARCHAR(32)  NOT NULL,
    rarity          VARCHAR(16)  NOT NULL,
    icon_url        VARCHAR(512),
    condition_type  VARCHAR(32)  NOT NULL,
    condition_value INTEGER,
    sort_order      INTEGER      NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_badges_category CHECK (category IN ('streak','accuracy','speed','explore','battle')),
    CONSTRAINT ck_badges_rarity   CHECK (rarity IN ('common','rare','epic','legendary'))
);

COMMENT ON TABLE badges IS '徽章定义表(15枚种子, 方向六)';

CREATE INDEX IF NOT EXISTS idx_badges_category ON badges (category);
CREATE INDEX IF NOT EXISTS idx_badges_rarity   ON badges (rarity);

-- ============================================================
-- 4. 学生徽章关联表（幂等：一个学生同一徽章一条记录）
-- ============================================================
CREATE TABLE IF NOT EXISTS student_badges (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id      UUID         NOT NULL REFERENCES students (id) ON DELETE CASCADE,
    badge_id        UUID         NOT NULL REFERENCES badges (id)    ON DELETE CASCADE,
    unlocked_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    unlock_metadata JSONB,
    CONSTRAINT uq_student_badges UNIQUE (student_id, badge_id)
);

COMMENT ON TABLE student_badges                  IS '学生徽章关联表(方向六)';
COMMENT ON COLUMN student_badges.unlock_metadata IS 'JSON：触发上下文(如波次、得分)';

CREATE INDEX IF NOT EXISTS idx_student_badges_student ON student_badges (student_id);
CREATE INDEX IF NOT EXISTS idx_student_badges_badge   ON student_badges (badge_id);

-- ============================================================
-- 5. 排行榜快照表（周榜/月榜，定时任务刷新）
-- ============================================================
CREATE TABLE IF NOT EXISTS leaderboard (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id    UUID        NOT NULL REFERENCES students (id) ON DELETE CASCADE,
    rank_type     VARCHAR(16) NOT NULL,
    rank_period   VARCHAR(16) NOT NULL,
    rank_position INTEGER     NOT NULL,
    score         INTEGER     NOT NULL,
    games_count   INTEGER     NOT NULL DEFAULT 0,
    best_wave     INTEGER     NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_leaderboard_type           CHECK (rank_type IN ('weekly','monthly')),
    CONSTRAINT uq_leaderboard_period_student UNIQUE (rank_type, rank_period, student_id)
);

COMMENT ON TABLE leaderboard                    IS '排行榜快照表(周榜/月榜, 方向六)';
COMMENT ON COLUMN leaderboard.rank_period       IS '周期标识：2026W38 或 2026-09';

CREATE INDEX IF NOT EXISTS idx_leaderboard_period
    ON leaderboard (rank_type, rank_period, rank_position);
CREATE INDEX IF NOT EXISTS idx_leaderboard_student
    ON leaderboard (student_id);

-- ============================================================
-- 6. 扩展 questions 表支持生活场景题库
-- ============================================================
ALTER TABLE questions ADD COLUMN IF NOT EXISTS scenario_type     VARCHAR(32);
ALTER TABLE questions ADD COLUMN IF NOT EXISTS scenario_template TEXT;
ALTER TABLE questions ADD COLUMN IF NOT EXISTS scenario_params   JSONB;

COMMENT ON COLUMN questions.scenario_type     IS '场景类型：shopping/cake/time/distance';
COMMENT ON COLUMN questions.scenario_template IS '题目模板(含占位符)';
COMMENT ON COLUMN questions.scenario_params   IS 'JSON：模板参数范围';

CREATE INDEX IF NOT EXISTS idx_questions_scenario
    ON questions (scenario_type)
    WHERE scenario_type IS NOT NULL;

-- ============================================================
-- 7. 初始化 15 枚徽章（固定 UUID 主键保证幂等）
-- ============================================================
INSERT INTO badges (id, name, display_name, description, category, rarity, icon_url, condition_type, condition_value, sort_order) VALUES
('00000000-0000-0000-0000-000000000001', 'streak_novice',     '连胜新手',   '连续答对3题',            'streak',   'common',    '🥉', 'max_streak',     3,   1),
('00000000-0000-0000-0000-000000000002', 'streak_pro',        '连胜高手',   '连续答对10题',           'streak',   'rare',      '🥈', 'max_streak',     10,  2),
('00000000-0000-0000-0000-000000000003', 'streak_master',     '连胜大师',   '连续答对25题',           'streak',   'epic',      '🥇', 'max_streak',     25,  3),
('00000000-0000-0000-0000-000000000004', 'streak_legend',     '连胜传奇',   '连续答对50题',           'streak',   'legendary', '👑', 'max_streak',     50,  4),
('00000000-0000-0000-0000-000000000005', 'accuracy_perfect',  '准星',      '单局准确率100%',         'accuracy', 'common',    '🎯', 'accuracy_perfect', 1,   5),
('00000000-0000-0000-0000-000000000006', 'accuracy_100',      '百发百中',  '累计100题准确率100%',    'accuracy', 'epic',      '🏹', 'accuracy_total', 100,  6),
('00000000-0000-0000-0000-000000000007', 'speed_lightning',   '闪电手',    '单题平均用时<3秒',       'speed',    'rare',      '⚡', 'avg_time_sec',    3,   7),
('00000000-0000-0000-0000-000000000008', 'speed_god',         '神算子',    '单题平均用时<1.5秒',     'speed',    'legendary', '🔥', 'avg_time_sec',    1,   8),
('00000000-0000-0000-0000-000000000009', 'explore_first',     '初次尝试',  '首次完成游戏',           'explore',  'common',    '🎮', 'first_game',      1,   9),
('00000000-0000-0000-0000-000000000010', 'explore_challenger','挑战者',    '累计完成10局游戏',       'explore',  'rare',      '🏆', 'total_games',    10,  10),
('00000000-0000-0000-0000-000000000011', 'explore_veteran',   '游戏达人',  '累计完成100局游戏',      'explore',  'epic',      '💎', 'total_games',   100,  11),
('00000000-0000-0000-0000-000000000012', 'explore_scenario',  '生活数学家', '首次完成场景题',         'explore',  'rare',      '🌍', 'first_scenario',  1,  12),
('00000000-0000-0000-0000-000000000013', 'battle_wave1',      '初出茅庐',  '战斗模式通关第1波',      'battle',   'common',    '🛡️', 'battle_wave',     1,  13),
('00000000-0000-0000-0000-000000000014', 'battle_wave5',      '数学骑士',  '战斗模式通关第5波',      'battle',   'rare',      '⚔️', 'battle_wave',     5,  14),
('00000000-0000-0000-0000-000000000015', 'battle_wave10',     '屠龙勇士',  '战斗模式通关第10波',     'battle',   'legendary', '🐉', 'battle_wave',    10,  15)
ON CONFLICT (id) DO NOTHING;

COMMIT;