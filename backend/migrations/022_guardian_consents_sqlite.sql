-- 022 · 监护人同意留痕表 guardian_consents（V6.5 T13 合规）· SQLite 方言
--
-- 与 PG 版的差异（参照既有约定）：
--   * UUID → CHAR(36)；TIMESTAMPTZ → TEXT；无 COMMENT ON
--   * 表级 UNIQUE / INDEX 语法相同
--   * 外键 ON DELETE CASCADE 生效依赖 PRAGMA foreign_keys=ON
--
-- 幂等说明：CREATE TABLE / INDEX 均带 IF NOT EXISTS，可重复执行。
-- 开发/测试库由 Base.metadata.create_all 建出（模型已注册），
-- 本文件用于手动对齐或离线 SQLite 部署。

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS guardian_consents (
    id               CHAR(36)     PRIMARY KEY,
    user_id          CHAR(36)     NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    guardian_phone   VARCHAR(20),
    consent_version  VARCHAR(32)  NOT NULL,
    agreed_at        TEXT         NOT NULL,
    channel          VARCHAR(32),
    ip               VARCHAR(64),
    user_agent       VARCHAR(512),
    created_at       TEXT         NOT NULL,
    CONSTRAINT uq_guardian_consents_user_version UNIQUE (user_id, consent_version)
);

CREATE INDEX IF NOT EXISTS idx_gc_user_id
    ON guardian_consents (user_id);

COMMIT;
