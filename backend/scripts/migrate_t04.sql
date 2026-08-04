-- ============================================================================
--  T04 学习画像看板 · PostgreSQL 生产库迁移脚本
--  执行方式： psql "$DATABASE_URL" -f backend/scripts/migrate_t04.sql
--
--  【为什么需要这个文件】
--    SQLAlchemy 的 create_all() 只会「建缺失的表」，**不会**给已存在的表补列。
--    所以：新表 student_mastery 靠 create_all 也能出来，但 mistakes.note 这种
--    「老表加列」必须走 DDL。为保证生产/开发一致，两者都在本脚本里显式声明。
--
--  【SQLite 开发 / pytest 内存库】
--    无需执行本脚本：backend/tests/conftest.py 的 autouse fixture 会调用
--    Base.metadata.create_all(engine)，新表新列一次到位。
--    （SQLite 的 ALTER TABLE 不支持 IF NOT EXISTS，本脚本也不为它设计。）
--
--  【幂等性】
--    全部语句使用 IF NOT EXISTS，可重复执行。
--
--  依赖前置：001_init.sql（students / knowledge_points / mistakes 已建好）
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1) 新建 student_mastery（学生 × 知识点 掌握度物化缓存）
--
--    ⚠️ 核查结论(R1)：V4.1 的 001_init.sql 里**没有**这张表，只有 student_profile
--    （单行 JSON 画像）。所以这里是全新建表，不是给老表加列。
--    student_profile 保留不动：它服务于 AI 提示词，本表服务于看板算法。
--
--    UUID 主键一律由 Python 侧 uuid4() 生成，不用 PG 专属 gen_random_uuid()，
--    与 app/models/base.py 的跨方言约定保持一致。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS student_mastery (
    id                  UUID            PRIMARY KEY,
    student_id          UUID            NOT NULL
                            REFERENCES students(id) ON DELETE CASCADE,
    knowledge_point_id  UUID            NOT NULL
                            REFERENCES knowledge_points(id) ON DELETE CASCADE,

    -- 掌握度核心指标
    mastery_score       DOUBLE PRECISION NOT NULL DEFAULT 0,
    mastery_level       INTEGER          NOT NULL DEFAULT 0,
    attempts            INTEGER          NOT NULL DEFAULT 0,
    correct_count       INTEGER          NOT NULL DEFAULT 0,
    error_rate          DOUBLE PRECISION NOT NULL DEFAULT 0,
    effective_attempts  DOUBLE PRECISION NOT NULL DEFAULT 0,

    -- T04 新增：错因摘要 + 时效戳（OQ4）
    error_summary       JSONB            NULL,
    last_practiced_at   TIMESTAMPTZ      NULL,
    computed_at         TIMESTAMPTZ      NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_student_mastery_student_kp UNIQUE (student_id, knowledge_point_id)
);

COMMENT ON TABLE  student_mastery                     IS '学生知识点掌握度物化表(T04)';
COMMENT ON COLUMN student_mastery.mastery_score       IS '掌握度 0~1（贝叶斯平滑）';
COMMENT ON COLUMN student_mastery.mastery_level       IS '0未入门/1薄弱/2基本掌握/3熟练';
COMMENT ON COLUMN student_mastery.effective_attempts  IS '时效衰减后的有效样本量 Σdecay';
COMMENT ON COLUMN student_mastery.error_summary       IS '错因分布 JSON，如 {"CALC":3,"STEP":1}';
COMMENT ON COLUMN student_mastery.last_practiced_at   IS '最近练习时间；> computed_at 表示缓存已脏';
COMMENT ON COLUMN student_mastery.computed_at         IS '本行计算时间，配合 TTL(900s) 判定新鲜度';

-- 若表在更早版本被建过（缺列场景），这几条 ALTER 负责补齐；已存在则跳过。
ALTER TABLE student_mastery ADD COLUMN IF NOT EXISTS error_summary      JSONB            NULL;
ALTER TABLE student_mastery ADD COLUMN IF NOT EXISTS last_practiced_at  TIMESTAMPTZ      NULL;
ALTER TABLE student_mastery ADD COLUMN IF NOT EXISTS computed_at        TIMESTAMPTZ      NOT NULL DEFAULT NOW();
ALTER TABLE student_mastery ADD COLUMN IF NOT EXISTS effective_attempts DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE student_mastery ADD COLUMN IF NOT EXISTS correct_count      INTEGER          NOT NULL DEFAULT 0;
ALTER TABLE student_mastery ADD COLUMN IF NOT EXISTS error_rate         DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE student_mastery ADD COLUMN IF NOT EXISTS attempts           INTEGER          NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_student_mastery_student
    ON student_mastery (student_id);
CREATE INDEX IF NOT EXISTS idx_student_mastery_computed
    ON student_mastery (student_id, computed_at);
CREATE INDEX IF NOT EXISTS idx_student_mastery_last_practiced
    ON student_mastery (last_practiced_at);

-- ---------------------------------------------------------------------------
-- 2) mistakes 加列：note（学生标记「已掌握」时的自述备注）
--
--    PUT /api/mistake/{id}/solve 的响应契约里有 note 字段。
--    不复用 error_reason —— 那是 AI 写的错因分析，语义不同，混用会污染错因聚类。
-- ---------------------------------------------------------------------------
ALTER TABLE mistakes ADD COLUMN IF NOT EXISTS note TEXT NULL;
COMMENT ON COLUMN mistakes.note IS '攻克备注(学生自述)，T04 新增';

-- 看板按 (student_id, solved) 高频过滤，001_init.sql 已建 idx_mistakes_student_solved，
-- 这里补一个按创建时间的索引，服务 /api/profile/error-patterns 的 range_days 窗口查询。
CREATE INDEX IF NOT EXISTS idx_mistakes_student_created
    ON mistakes (student_id, created_at);

COMMIT;

-- ============================================================================
--  回滚（仅在需要时手工执行，生产慎用）
-- ----------------------------------------------------------------------------
--  BEGIN;
--  DROP TABLE IF EXISTS student_mastery;
--  ALTER TABLE mistakes DROP COLUMN IF EXISTS note;
--  DROP INDEX IF EXISTS idx_mistakes_student_created;
--  COMMIT;
-- ============================================================================
