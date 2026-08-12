# 2026-08-11 数据补全日志

> 练习模块完整重新设计（T1–T11）的数据侧脚本产物。
> 跑完每个脚本会在本文件追加一段；运行时间由脚本内部写入。
> **不改任何 schema** —— 所有动作都是 ``UPDATE`` 现有行脏值/缺值；备份表用 ``CREATE TABLE … AS SELECT`` 临时存放（不算 DDL 改动）。

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-11 15:10
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 641 道
- 命中归档：55 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-11 15:10
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 2176 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-11 15:10
- 模式：dry-run
- active 无 kp 全库：380 题
- 本轮候选扫描：378 题
- 自动打标：14 题
- 待人工标注：364 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-11 18:00
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-11 18:00
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-11 18:00
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-11 18:02
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-11 18:02
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-11 18:02
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-11 18:03
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-11 18:03
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-11 18:03
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-11 18:16
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-11 18:16
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-11 18:16
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-11 18:26
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-11 18:26
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-11 18:26
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-11 18:28
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-11 18:28
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-11 18:28
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-11 18:31
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-11 18:31
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-11 18:31
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-12 02:47
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 641 道
- 命中归档：55 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-12 02:47
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 2176 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-12 02:47
- 模式：dry-run
- active 无 kp 全库：380 题
- 本轮候选扫描：378 题
- 自动打标：14 题
- 待人工标注：364 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-12 02:52
- 备份表：questions_options_backup_20260811（已创建）
- 模式：实际执行
- 扫描：active 选择题 641 道
- 命中归档：55 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-12 02:52
- 备份表：questions_content_backup_20260811（已创建）
- 模式：实际执行
- 扫描：questions 全表 2176 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-12 02:52
- 模式：实际执行
- active 无 kp 全库：361 题
- 本轮候选扫描：359 题
- 自动打标：14 题
- 待人工标注：345 题
- 导出 CSV：D:\制作网页\cuotixiaolaoshi\docs\unmapped-human-triage-20260811.csv
- 内置关键词数：16

## 阶段 C-3 · 人工标注回填

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py --replay
- 时间：2026-08-12 03:08
- CSV：D:\制作网页\cuotixiaolaoshi\docs\unmapped-human-triage-20260811.filled.csv
- 模式：实际执行
- 回填 kp_id：307 题

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-12 05:13
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-12 05:13
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-12 05:13
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-12 05:14
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-12 05:14
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-12 05:14
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

