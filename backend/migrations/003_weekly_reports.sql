-- 003 · weekly_reports 家长周报快照表（Epic6 · T1） · PostgreSQL 方言
--
-- 背景：student_mastery 是 upsert 覆盖式写入、无任何历史，
--       因此新建本表在生成周报时把当周数据整体存档，供下周做环比 diff。
--
-- 幂等说明：全部语句均带 IF NOT EXISTS，可重复执行。
-- 执行方式：psql -f backend/migrations/003_weekly_reports.sql
-- SQLite 版本见同目录 003_weekly_reports_sqlite.sql。

CREATE TABLE IF NOT EXISTS weekly_reports (
    id              UUID         PRIMARY KEY,
    student_id      UUID         NOT NULL REFERENCES students (id) ON DELETE CASCADE,
    period_start    TIMESTAMPTZ  NOT NULL,
    period_end      TIMESTAMPTZ  NOT NULL,
    token           VARCHAR(64)  NOT NULL,
    expires_at      TIMESTAMPTZ  NOT NULL,
    view_count      INTEGER      NOT NULL DEFAULT 0,
    snapshot_json   JSONB        NOT NULL DEFAULT '{}'::jsonb,
    report_text     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_weekly_reports_student_period UNIQUE (student_id, period_start),
    CONSTRAINT uq_weekly_reports_token          UNIQUE (token)
);

COMMENT ON TABLE  weekly_reports               IS '家长周报快照表(Epic6)';
COMMENT ON COLUMN weekly_reports.period_start  IS '周期起点(UTC)，对应北京时间周一00:00:00';
COMMENT ON COLUMN weekly_reports.period_end    IS '周期终点(UTC)，对应北京时间周日23:59:59';
COMMENT ON COLUMN weekly_reports.token         IS '分享令牌 secrets.token_urlsafe(24)；重新生成不换';
COMMENT ON COLUMN weekly_reports.expires_at    IS '令牌过期时刻(UTC)，生成时刻+30天';
COMMENT ON COLUMN weekly_reports.view_count    IS '家长打开分享页累计次数(U4)';
COMMENT ON COLUMN weekly_reports.snapshot_json IS '当周快照：掌握度+错因+计数(设计文档 §4.2)';
COMMENT ON COLUMN weekly_reports.report_text   IS 'Report Agent 输出：headline/三段文案/source(§4.3)';

-- 查上一份快照 / 历史列表
CREATE INDEX IF NOT EXISTS idx_weekly_reports_student_period
    ON weekly_reports (student_id, period_start DESC);

-- 公开分享接口的查询路径
CREATE INDEX IF NOT EXISTS idx_weekly_reports_token_expires
    ON weekly_reports (token, expires_at);
