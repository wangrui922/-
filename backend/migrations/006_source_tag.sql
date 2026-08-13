-- 006 · questions 表新增 source_tag 列（溯源标记 · T06 · examprep 借鉴）
--
-- 语义：green=来自教材/资料且已核验(讲解可直接信任) /
--       yellow=AI 生成补充(正确但建议抽检) / red=疑似编造或未经核验(进待抽检队列, 不抽题)。
-- 回填策略：AI 生成归档题(source_doc='ai_gen')标 red，其余(docx 资料题)标 green。

ALTER TABLE questions ADD COLUMN source_tag VARCHAR(8) NOT NULL DEFAULT 'green';

UPDATE questions SET source_tag = 'red' WHERE source_doc = 'ai_gen';
UPDATE questions SET source_tag = 'green' WHERE source_tag IS NULL OR source_tag <> 'red';
