-- 005 · mistakes 表新增 kp_id 列（错题→知识点深链） · PostgreSQL 方言
--
-- 新增列：
--   kp_id  关联知识点（建档时从 Question.kp_id 快照写入，FK→knowledge_points.id）
--
-- 快照语义：
--   Mistake.kp_id 是建档时的**快照**，不是实时 JOIN。
--   题目的 kp 后续被修正时，历史错题的归因不应被追溯改写。
--
-- 幂等说明：全部语句带 IF NOT EXISTS，可重复执行。

BEGIN;

-- 新增 kp_id 列：FK→knowledge_points.id，ON DELETE SET NULL，nullable，建索引
ALTER TABLE mistakes ADD COLUMN IF NOT EXISTS kp_id UUID REFERENCES knowledge_points(id) ON DELETE SET NULL;

-- 回填（与 ADD COLUMN 同事务）：存量错题从题目继承 kp_id
UPDATE mistakes SET kp_id = (
    SELECT q.kp_id FROM questions q WHERE q.id = mistakes.question_id
) WHERE mistakes.kp_id IS NULL;

COMMIT;

-- 按知识点筛错题的查询路径
CREATE INDEX IF NOT EXISTS idx_mistakes_kp ON mistakes (kp_id);

COMMENT ON COLUMN mistakes.kp_id IS '关联知识点(建档时从Question.kp_id快照，不被后续修正追溯改写)';
