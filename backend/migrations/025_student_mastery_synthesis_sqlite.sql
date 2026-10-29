-- 025 · 补建 L3 综合洞察层 student_mastery_synthesis 表（SQLite 方言）
-- 幂等（IF NOT EXISTS），对已有库无副作用。
-- 表由 app/services/student_dna_service.consolidate_mastery 以 raw SQL 维护
-- （无 ORM 模型，参照 review_cards / review_logs 既有范式，进
--  backend/scripts/schema_reconcile.py 对账 + 冷母库 _master_dev.db 同步，
--  对齐「工程铁律 3 / 11」）。
-- 溯源边 ref_kp_ids / ref_event_ids 为 JSON 数组文本，使 L3→L2→L1 证据链可审计、
-- 可回滚（设计文档 docs/design-c02-three-layer-student-model.md §3/§5/§6）。

CREATE TABLE IF NOT EXISTS student_mastery_synthesis (
    id                  CHAR(36) PRIMARY KEY,
    student_id          CHAR(36) NOT NULL,
    synthesis_type      TEXT NOT NULL
                        CHECK (synthesis_type IN ('ERROR_PATTERN','WEAK_CHAIN','TREND')),
    ref_kp_ids          TEXT,                       -- JSON 数组：溯源 L2 行
    ref_event_ids       TEXT,                       -- JSON 数组：溯源 L1 事件
    payload             TEXT,                       -- JSON：综合结论
    confidence          REAL NOT NULL DEFAULT 0.0,  -- 0~1，随样本量增长
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (student_id, synthesis_type)             -- 每类每生一行，upsert
);

CREATE INDEX IF NOT EXISTS idx_sms_student ON student_mastery_synthesis(student_id);
