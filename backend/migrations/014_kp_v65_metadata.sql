-- 014 · knowledge_points 增加 V6.5 学习元数据 7 列 + 时间戳（KG-001） · PostgreSQL 方言
--
-- 新增列：
--   subject     学科（当前仅 math，字段保留扩展位）
--   grade       适用年级（小学3=3 … 初三=9；L1 聚合节点可为 NULL）
--   semester    学期（上 / 下）
--   chapter     所属章节（主干章节名，L1 节点为自身 name 或 NULL）
--   difficulty  难度档位 1/3/5（默认 3）
--   importance  重要程度 1-5（默认 3，Priority 权重 0.20 的取值来源）
--   status      状态 active / archived（默认 active）
--   created_at / updated_at  创建/更新时间（UTC，与 TimestampMixin 一致）
--
-- 幂等说明：全部 ALTER 带 IF NOT EXISTS，可重复执行。
-- 回填说明：存量行只回填学科/难度/重要程度/状态的保守默认值；
--           年级/学期/章节的细分标注由 seed_kp_v65.py 按 code 逐一补齐，
--           避免在 SQL 层臆断语义。

BEGIN;

ALTER TABLE knowledge_points ADD COLUMN IF NOT EXISTS subject     VARCHAR(16)  NOT NULL DEFAULT 'math';
ALTER TABLE knowledge_points ADD COLUMN IF NOT EXISTS grade       SMALLINT;
ALTER TABLE knowledge_points ADD COLUMN IF NOT EXISTS semester    VARCHAR(8);
ALTER TABLE knowledge_points ADD COLUMN IF NOT EXISTS chapter     VARCHAR(64);
ALTER TABLE knowledge_points ADD COLUMN IF NOT EXISTS difficulty  SMALLINT     NOT NULL DEFAULT 3;
ALTER TABLE knowledge_points ADD COLUMN IF NOT EXISTS importance  SMALLINT     NOT NULL DEFAULT 3;
ALTER TABLE knowledge_points ADD COLUMN IF NOT EXISTS status      VARCHAR(16)  NOT NULL DEFAULT 'active';
ALTER TABLE knowledge_points ADD COLUMN IF NOT EXISTS created_at  TIMESTAMPTZ  NOT NULL DEFAULT now();
ALTER TABLE knowledge_points ADD COLUMN IF NOT EXISTS updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now();

-- 回填（与 ADD COLUMN 同事务）：存量知识点视为 math / active / 难度3 / 重要3
UPDATE knowledge_points SET subject = 'math' WHERE subject IS NULL OR subject = '';
UPDATE knowledge_points SET status = 'active' WHERE status IS NULL OR status = '';

COMMIT;

COMMENT ON COLUMN knowledge_points.subject     IS '学科(math)';
COMMENT ON COLUMN knowledge_points.grade       IS '适用年级(小学3=3 … 初三=9)';
COMMENT ON COLUMN knowledge_points.semester    IS '学期(上/下)';
COMMENT ON COLUMN knowledge_points.chapter     IS '所属章节';
COMMENT ON COLUMN knowledge_points.difficulty  IS '难度档位(1/3/5)';
COMMENT ON COLUMN knowledge_points.importance  IS '重要程度(1-5)';
COMMENT ON COLUMN knowledge_points.status      IS '状态(active/archived)';