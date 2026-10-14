-- 015 · 知识点关系边表 knowledge_relations（KG-002）· SQLite 方言
--
-- 与 PG 版的差异（参照既有约定）：
--   * UUID → CHAR(36)（Python 侧确定性派生，不用 gen_random_uuid()）
--   * TIMESTAMPTZ → TEXT（ISO 8601 字符串，由 SQLAlchemy DateTime 写入）
--   * 无 COMMENT ON 语法，注释写在行内
--   * CHECK / UNIQUE 表级约束写法相同，SQLite 均支持
--   * 外键 ON DELETE CASCADE 生效依赖 app/db.py 的 PRAGMA foreign_keys=ON
--
-- 幂等说明：CREATE TABLE / INDEX 均带 IF NOT EXISTS，可重复执行。
-- 开发/测试库由 Base.metadata.create_all 建出（模型已注册），
-- 本文件用于手动对齐或离线 SQLite 部署。

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS knowledge_relations (
    id            CHAR(36)    PRIMARY KEY,
    source_id     CHAR(36)    NOT NULL REFERENCES knowledge_points (id) ON DELETE CASCADE,
    target_id     CHAR(36)    NOT NULL REFERENCES knowledge_points (id) ON DELETE CASCADE,
    relation_type VARCHAR(16) NOT NULL,
    created_at    TEXT        NOT NULL,
    updated_at    TEXT        NOT NULL,
    CONSTRAINT ck_knowledge_relations_type       CHECK (relation_type IN ('prerequisite','related','next')),
    CONSTRAINT ck_knowledge_relations_no_self    CHECK (source_id <> target_id),
    CONSTRAINT uq_knowledge_relations_edge       UNIQUE (source_id, target_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_relations_target_type
    ON knowledge_relations (target_id, relation_type);

COMMIT;