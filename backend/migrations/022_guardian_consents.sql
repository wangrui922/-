-- 022 · 监护人同意留痕表 guardian_consents（V6.5 T13 合规）· PostgreSQL 方言
--
-- 背景：V6.5 T13 合规动作。运营主体为个体工商户（红塔景曜商贸工作室），
-- 产品用户含未成年人。按《儿童个人信息网络保护规定》第八条、第九条，
-- 处理不满十四周岁未成年人个人信息前，应当以显著、清晰的方式告知儿童
-- 监护人并征得其同意；按第十条，同意须留痕备查。
--
-- 设计（与 app/models/compliance.py::GuardianConsent 逐字对齐，勿漂移）：
--   * user_id 指向 users.id，账号删除级联删除同意记录（CASCADE）；
--   * (user_id, consent_version) 唯一：同一账号同一规则版本只留一条，
--     服务层重复提交幂等返回已有记录，不报错；
--   * agreed_at 记录监护人同意动作发生时间（UTC），created_at 记录落库时间，
--     两者分开便于合规举证；
--   * ip / user_agent / channel 为同意取证字段（同意发生的位置与渠道）。
--
-- 幂等说明：CREATE TABLE / INDEX 均带 IF NOT EXISTS，可重复执行。
-- 执行方式：psql -U coach -h 127.0.0.1 -p 5433 -d coach -f backend/migrations/022_guardian_consents.sql
-- SQLite 版本见同目录 022_guardian_consents_sqlite.sql。

BEGIN;

CREATE TABLE IF NOT EXISTS guardian_consents (
    id               UUID        PRIMARY KEY,
    user_id          UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    guardian_phone   VARCHAR(20),
    consent_version  VARCHAR(32) NOT NULL,
    agreed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    channel          VARCHAR(32),
    ip               VARCHAR(64),
    user_agent       VARCHAR(512),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_guardian_consents_user_version UNIQUE (user_id, consent_version)
);

CREATE INDEX IF NOT EXISTS idx_gc_user_id
    ON guardian_consents (user_id);

COMMENT ON TABLE  guardian_consents              IS '监护人同意留痕表(V6.5 T13)';
COMMENT ON COLUMN guardian_consents.user_id      IS '同意所属账号(users.id)';
COMMENT ON COLUMN guardian_consents.guardian_phone IS '监护人手机号';
COMMENT ON COLUMN guardian_consents.consent_version IS '同意的规则版本号';
COMMENT ON COLUMN guardian_consents.agreed_at    IS '监护人同意时间(UTC)';
COMMENT ON COLUMN guardian_consents.channel      IS '提交渠道 web/miniapp';
COMMENT ON COLUMN guardian_consents.ip           IS '提交IP(合规取证)';
COMMENT ON COLUMN guardian_consents.user_agent   IS '客户端UA(合规取证)';

COMMIT;
