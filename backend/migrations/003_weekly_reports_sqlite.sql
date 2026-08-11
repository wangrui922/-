-- 003 · weekly_reports 家长周报快照表（Epic6 · T1） · SQLite 方言
--
-- 与 PG 版的差异（参照 001_init_sqlite.sql 的既有约定）：
--   * UUID  → CHAR(36)（Python 侧 uuid4() 生成，不用 gen_random_uuid()）
--   * JSONB → TEXT（app/models/base.py::JSONType 负责 json 序列化）
--   * TIMESTAMPTZ → TEXT（ISO 8601 字符串）
--   * 无 COMMENT ON 语法，注释写在行内
--
-- 幂等说明：CREATE TABLE / INDEX 均带 IF NOT EXISTS，可重复执行。
-- 开发与测试环境通常无需手工执行：Base.metadata.create_all() 会直接建表。

CREATE TABLE IF NOT EXISTS weekly_reports (
    id              CHAR(36)     PRIMARY KEY,                    -- uuid4()
    student_id      CHAR(36)     NOT NULL REFERENCES students (id) ON DELETE CASCADE,
    period_start    TEXT         NOT NULL,                       -- UTC，北京时间周一00:00:00
    period_end      TEXT         NOT NULL,                       -- UTC，北京时间周日23:59:59
    token           VARCHAR(64)  NOT NULL,                       -- secrets.token_urlsafe(24)
    expires_at      TEXT         NOT NULL,                       -- UTC，生成时刻+30天
    view_count      INTEGER      NOT NULL DEFAULT 0,             -- 家长打开次数(U4)
    snapshot_json   TEXT         NOT NULL DEFAULT '{}',          -- 快照 JSON(设计文档 §4.2)
    report_text     TEXT         NOT NULL DEFAULT '{}',          -- Report Agent 输出(§4.3)
    created_at      TEXT         NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at      TEXT         NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    CONSTRAINT uq_weekly_reports_student_period UNIQUE (student_id, period_start),
    CONSTRAINT uq_weekly_reports_token          UNIQUE (token)
);

-- 查上一份快照 / 历史列表
CREATE INDEX IF NOT EXISTS idx_weekly_reports_student_period
    ON weekly_reports (student_id, period_start DESC);

-- 公开分享接口的查询路径
CREATE INDEX IF NOT EXISTS idx_weekly_reports_token_expires
    ON weekly_reports (token, expires_at);
