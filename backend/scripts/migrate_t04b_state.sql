-- AI数学教练 V4 · T04B 状态机落库迁移（生产 PostgreSQL）
--
-- 说明：
--   * 开发环境（SQLite）走 Base.metadata.create_all 即可自动建列，无需本脚本；
--   * 生产环境已存在 ai_sessions 表时，用本脚本补齐三列；
--   * 已有会话统一视为引导中（guiding）。

ALTER TABLE ai_sessions ADD COLUMN IF NOT EXISTS session_state VARCHAR(16) NOT NULL DEFAULT 'intro';
ALTER TABLE ai_sessions ADD COLUMN IF NOT EXISTS step INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_sessions ADD COLUMN IF NOT EXISTS state_updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

COMMENT ON COLUMN ai_sessions.session_state IS 'intro/guiding/practicing/summarizing/closed';
COMMENT ON COLUMN ai_sessions.step IS '辅导轮次步数';
COMMENT ON COLUMN ai_sessions.state_updated_at IS '状态最后更新时间(UTC)';
