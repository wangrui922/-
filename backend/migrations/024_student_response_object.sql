-- 024 · ai_messages 增加学生回答结构化列 response_object（TASK-002 V1）· PostgreSQL 方言
--
-- 设计：docs/task-002-response-object-design.md §5。A 方案（合并 JSON 列），
-- 不建新表；raw_response 即现有 content 列，不复制；老消息列值 NULL，读取方
-- 按「无结构化信号」降级（回捞入口 orchestrator.latest_response_objects）。
-- 幂等：PG 9.6+ 的 ADD COLUMN IF NOT EXISTS。
-- 执行：psql -U coach -h 127.0.0.1 -p 5433 -d coach -f backend/migrations/024_student_response_object.sql
-- SQLite 版本见同目录 024_student_response_object_sqlite.sql。

BEGIN;

ALTER TABLE ai_messages ADD COLUMN IF NOT EXISTS response_object JSONB;

COMMENT ON COLUMN ai_messages.response_object
    IS '学生回答结构化对象(JSON):response_type/answer/reasoning/evidence/error/confidence/step_state;仅user消息;老数据NULL;可凭content重分析';

COMMIT;
