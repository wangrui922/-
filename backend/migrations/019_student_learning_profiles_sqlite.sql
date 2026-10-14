-- 019 · 学生画像表 student_learning_profiles（DNA-001/DNA-002）· SQLite 方言
--
-- 与 PG 版的差异（参照既有约定）：
--   * UUID → CHAR(36)（Python 侧 uuid4 生成，不用 gen_random_uuid()）
--   * TIMESTAMPTZ → TEXT（ISO 8601 字符串，由 SQLAlchemy DateTime 写入）
--   * JSONB → TEXT（json 序列化，由 JSONType 处理）
--   * 无 COMMENT ON 语法，注释写在行内
--   * CHECK / UNIQUE 表级约束写法相同，SQLite 均支持
--   * 外键 ON DELETE CASCADE 生效依赖 app/db.py 的 PRAGMA foreign_keys=ON
--
-- 幂等说明：CREATE TABLE / INDEX 均带 IF NOT EXISTS，可重复执行。
-- 开发/测试库由 Base.metadata.create_all 建出（模型已注册），
-- 本文件用于手动对齐或离线 SQLite 部署。

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS student_learning_profiles (
    id              CHAR(36)    PRIMARY KEY,
    student_id      CHAR(36)    NOT NULL UNIQUE REFERENCES students (id) ON DELETE CASCADE,
    learning_goal   VARCHAR(32) NOT NULL,
    learning_stage  VARCHAR(32),
    current_level   VARCHAR(32),
    learning_habits TEXT,
    common_errors   TEXT,
    hint_dependency VARCHAR(16),
    learning_trend  TEXT,
    created_at      TEXT        NOT NULL,
    updated_at      TEXT        NOT NULL,
    CONSTRAINT ck_student_learning_profiles_goal
        CHECK (learning_goal IN ('提高校内成绩','跟上进度','补基础','期中','期末','中考','高考','自主提高'))
);

COMMIT;