-- 026 · 同伴视角语料库 peer_view_corpus（SQLite 方言）
-- 幂等（IF NOT EXISTS），对已有库无副作用。
-- 表由 app/services/peer_corpus_service.py 以 raw SQL 维护
-- （无 ORM 模型，参照 025 student_mastery_synthesis 的既有范式，进
--  backend/scripts/schema_reconcile.py 对账 + 冷母库 _master_dev.db 同步，
--  对齐「工程铁律 3 / 11」）。
--
-- 设计来源：07-项目文档/规格-同伴视角语料库-20261029.md（PRD1 P0-5b）。
-- 流程：生成 → 规则过滤(禁止项) → archived → 人工抽检放行 → released；
-- 只有 status='released' 的语料可被讲解层（grounding_service）检索引用。
-- 未放行语料（draft/archived/rejected）进入线上讲解 0 例外（可回归验证）。

CREATE TABLE IF NOT EXISTS peer_view_corpus (
    id              CHAR(36) PRIMARY KEY,
    kp_code         TEXT NOT NULL,                 -- 挂靠知识点 code（328 个中选）
    grade           INTEGER NOT NULL
                    CHECK (grade BETWEEN 1 AND 6),  -- 小学 1-6 年级
    scene           TEXT,                          -- 场景标签：购物/时间/距离/吃/穿/游戏/校园/家务/手工
    error_type      TEXT,                          -- 错因类型：读题错/方法错/计算错/知识点缺/检查漏
    sentence        TEXT NOT NULL,                 -- 语料句本体，≤40 字、口语
    source          TEXT NOT NULL DEFAULT 'seed',  -- seed(人工)/template(模板生成)/llm_rewrite(模型改写)
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','archived','released','rejected')),
    reviewed_by     TEXT,                          -- 人工审核留痕
    reviewed_at     TEXT,                          -- 审核时间（UTC）
    forbidden_scan  TEXT,                          -- JSON 数组：机器扫描命中的禁止项 code（空数组=干净）
    seed_batch      TEXT,                          -- 批次号（运营分批改）
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pvc_status_kp  ON peer_view_corpus(status, kp_code);
CREATE INDEX IF NOT EXISTS idx_pvc_kp_grade  ON peer_view_corpus(kp_code, grade);
CREATE INDEX IF NOT EXISTS idx_pvc_status_err ON peer_view_corpus(status, error_type);
