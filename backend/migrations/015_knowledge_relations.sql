-- 015 · 知识点关系边表 knowledge_relations（KG-002）· PostgreSQL 方言
--
-- 背景：V6.5 知识树除归类树（knowledge_points.parent_id 自引用）外，
-- 还需「学习依赖/关联/顺序」三维关系边，支撑目标检测（KG-004）与
-- 推荐链路（KG-005）。本表与归类树正交，只看边不看层级。
--
-- 设计（与 app/models/content.py::KnowledgeRelation 逐字对齐，勿漂移）：
--   * source_id / target_id 外键指向 knowledge_points.id，ON DELETE CASCADE；
--   * relation_type 枚举 prerequisite/related/next（CHECK 约束）；
--   * source_id <> target_id（禁止自环）；
--   * (source_id, target_id, relation_type) 唯一（同一类型不重复建边）；
--   * idx_knowledge_relations_target_type 加速「按目标点查前置/关联」。
--
-- 幂等说明：CREATE TABLE / INDEX 均带 IF NOT EXISTS，可重复执行。
-- 执行方式：psql -U coach -h 127.0.0.1 -p 5433 -d coach -f backend/migrations/015_knowledge_relations.sql
-- SQLite 版本见同目录 015_knowledge_relations_sqlite.sql。

BEGIN;

CREATE TABLE IF NOT EXISTS knowledge_relations (
    id            UUID         PRIMARY KEY,
    source_id     UUID         NOT NULL REFERENCES knowledge_points (id) ON DELETE CASCADE,
    target_id     UUID         NOT NULL REFERENCES knowledge_points (id) ON DELETE CASCADE,
    relation_type VARCHAR(16)  NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_knowledge_relations_type       CHECK (relation_type IN ('prerequisite','related','next')),
    CONSTRAINT ck_knowledge_relations_no_self    CHECK (source_id <> target_id),
    CONSTRAINT uq_knowledge_relations_edge       UNIQUE (source_id, target_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_relations_target_type
    ON knowledge_relations (target_id, relation_type);

COMMENT ON TABLE  knowledge_relations                 IS '知识点关系边表(KG-002)：prerequisite/related/next';
COMMENT ON COLUMN knowledge_relations.source_id       IS '源知识点（需先掌握/关联起点/顺序起点）';
COMMENT ON COLUMN knowledge_relations.target_id       IS '目标知识点';
COMMENT ON COLUMN knowledge_relations.relation_type   IS '关系类型 prerequisite/related/next';

COMMIT;