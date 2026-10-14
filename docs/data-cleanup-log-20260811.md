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

## 公式压扁题清洗 · 2026-08-19

- 原因：旧版页面 / Word-PDF 抽取把分式、乘除号和上下标压成无序字符串，学生无法读题；不能用模型根据选项猜回原题。
- 扫描范围：当前主库与隔离验收库的 active 题；命中 Unicode 私用区公式字形或运算符倒序特征。
- 可核对修复：3 道（完全数示例、`1/n + 1/n + 1/n = 1/4`、`a × 3/5（ ）a ÷ 3/5`）。
- 保守下架：3 道（分式结构或乘除系数无法唯一恢复），仅执行 `status='archived'`、`source_tag='red'`、`ship_ready=0`，原题和答案保留。
- 另外同步下架种子中 4 道同类旧题，防止重建数据库时复活。
- 审计表：`questions_formula_cleanup_backup_20260819`；数据库写入前使用 SQLite backup API 生成一致性备份。
- 防回归：`quality_gate` 与 `extract_bank` 均拦截同类污染；定向测试 23 项通过。
- 全量测试：除本次变更外，原有 3 个清洗脚本测试因仓库测试路径指向不存在的 `backend/scripts` 上级目录而失败；另 2 个 CLI help 测试受同一 Windows 子进程输出读取问题影响，未发现业务断言失败。

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

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:19:17.128985+00:00
- 模式：dry-run
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：（未写出）
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:19:19.248136+00:00
- 模式：dry-run
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：（未写出）
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:19:20.250295+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:19:22.715446+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:19:24.721459+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:19:26.900593+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:19:27.639720+00:00
- 模式：dry-run
- 扫描：active 选择题 18 道
- 命中归档：10 道
- 期望核对：--expect 10
- 跳过的已补D id：N/A（dry-run）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：（未写出）

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:19:30.534891+00:00
- 模式：dry-run
- 扫描：active 选择题 18 道
- 命中归档：10 道
- 期望核对：--expect 10
- 跳过的已补D id：N/A（dry-run）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：（未写出）

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:19:36.120677+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:19:39.032183+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:19:40.210798+00:00
- 模式：实际执行
- 扫描：active 选择题 18 道
- 命中归档：10 道
- 期望核对：--expect 10
- 跳过的已补D id：3 个（双保险）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-removed-20260812.csv

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:19:45.197095+00:00
- 模式：实际执行
- 扫描：active 选择题 18 道
- 命中归档：10 道
- 期望核对：--expect 10
- 跳过的已补D id：3 个（双保险）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-removed-20260812.csv

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:20:02.041066+00:00
- 模式：dry-run
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：（未写出）
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:20:04.274756+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:20:07.683700+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:20:10.042952+00:00
- 模式：dry-run
- 扫描：active 选择题 18 道
- 命中归档：10 道
- 期望核对：--expect 10
- 跳过的已补D id：N/A（dry-run）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：（未写出）

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:20:17.991008+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:20:20.056699+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:20:21.122339+00:00
- 模式：实际执行
- 扫描：active 选择题 18 道
- 命中归档：10 道
- 期望核对：--expect 10
- 跳过的已补D id：3 个（双保险）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-removed-20260812.csv

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:21:42.142543+00:00
- 模式：dry-run
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：（未写出）
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:21:44.687167+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:21:47.708140+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:21:49.498727+00:00
- 模式：dry-run
- 扫描：active 选择题 18 道
- 命中归档：10 道
- 期望核对：--expect 10
- 跳过的已补D id：N/A（dry-run）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：（未写出）

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:21:54.201196+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:21:56.239205+00:00
- 模式：实际执行
- 扫描：active 选择题 18 道
- 命中归档：10 道
- 期望核对：--expect 10
- 跳过的已补D id：3 个（双保险）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-removed-20260812.csv

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:22:05.122928+00:00
- 模式：dry-run
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：（未写出）
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:22:07.178613+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:22:08.758260+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:22:10.220770+00:00
- 模式：dry-run
- 扫描：active 选择题 18 道
- 命中归档：10 道
- 期望核对：--expect 10
- 跳过的已补D id：N/A（dry-run）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：（未写出）

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:22:18.108927+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:22:22.152598+00:00
- 模式：实际执行
- 扫描：active 选择题 18 道
- 命中归档：10 道
- 期望核对：--expect 10
- 跳过的已补D id：3 个（双保险）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-removed-20260812.csv

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:22:45.562758+00:00
- 模式：dry-run
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：（未写出）
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:22:47.755847+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:22:49.980943+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:22:51.668059+00:00
- 模式：dry-run
- 扫描：active 选择题 18 道
- 命中归档：10 道
- 期望核对：--expect 10
- 跳过的已补D id：N/A（dry-run）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：（未写出）

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:22:57.166626+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:22:59.697529+00:00
- 模式：实际执行
- 扫描：active 选择题 18 道
- 命中归档：10 道
- 期望核对：--expect 10
- 跳过的已补D id：3 个（双保险）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-removed-20260812.csv

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:23:47.795022+00:00
- 模式：dry-run
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：（未写出）
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:23:48.954260+00:00
- 模式：dry-run
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：（未写出）
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:23:53.744485+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:23:56.007954+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:23:59.268444+00:00
- 模式：dry-run
- 扫描：active 选择题 18 道
- 命中归档：10 道
- 期望核对：--expect 10
- 跳过的已补D id：N/A（dry-run）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：（未写出）

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:24:00.656927+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:24:06.460308+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:24:08.303960+00:00
- 模式：实际执行
- 扫描：active 选择题 18 道
- 命中归档：10 道
- 期望核对：--expect 10
- 跳过的已补D id：3 个（双保险）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-removed-20260812.csv

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:24:12.759574+00:00
- 模式：dry-run
- 扫描：active 选择题 563 道
- 命中归档：308 道
- 期望核对：未设（跳过熔断）
- 跳过的已补D id：N/A（dry-run）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：（未写出）

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:24:30.586824+00:00
- 模式：dry-run
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：（未写出）
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:24:32.494735+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:24:34.421360+00:00
- 模式：实际执行
- 扫描：active 选择题 563 道
- 命中归档：308 道
- 期望核对：--expect 308
- 跳过的已补D id：3 个（双保险）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-removed-20260812.csv

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:24:34.803515+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:24:36.764347+00:00
- 模式：dry-run
- 扫描：active 选择题 18 道
- 命中归档：10 道
- 期望核对：--expect 10
- 跳过的已补D id：N/A（dry-run）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：（未写出）

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:24:40.534180+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:24:43.506504+00:00
- 模式：实际执行
- 扫描：active 选择题 18 道
- 命中归档：10 道
- 期望核对：--expect 10
- 跳过的已补D id：3 个（双保险）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-removed-20260812.csv

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:28:59.412354+00:00
- 模式：dry-run
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：（未写出）
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:29:17.290144+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:29:39.351513+00:00
- 模式：dry-run
- 扫描：active 选择题 255 道
- 命中归档：0 道
- 期望核对：未设（跳过熔断）
- 跳过的已补D id：N/A（dry-run）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：（未写出）

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:30:00.185630+00:00
- 模式：实际执行
- 扫描：active 选择题 255 道
- 命中归档：0 道
- 期望核对：未设（跳过熔断）
- 跳过的已补D id：3 个（双保险）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-removed-20260812.csv

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:41:57.346034+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\backend\_fixa_tmp\out\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:41:59.668991+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\backend\_fixa_tmp\out\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## N0.2 · 全库残缺选择题批量归档

- 脚本：scripts/data_cleanup/20260812_archive_residual_choices.py
- 时间：2026-08-12T07:55:24.854070+00:00
- 模式：dry-run
- 扫描：active 选择题 255 道
- 命中归档：0 道
- 期望核对：未设（跳过熔断）
- 跳过的已补D id：N/A（dry-run）
- 备份表：questions_removed_backup_20260812（reason=residual_choice_missing_abcd）
- CSV：（未写出）

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:56:20.313171+00:00
- 模式：dry-run
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：（未写出）
- 不动字段：correct_answer

## N0.1 · 截图 4 题即时修复

- 脚本：scripts/data_cleanup/20260812_fix_screenshot_four.py
- 时间：2026-08-12T07:56:33.829915+00:00
- 模式：实际执行
- 补 D：3 道（9a4f1461, a3275f03, 855ae9b6 → 末位补 D）
- 归档：1 道（9cf5259e 逻辑错误）
- 备份表：questions_options_backup_20260812（补D） / questions_removed_backup_20260812（archive）
- CSV：D:\制作网页\cuotixiaolaoshi\docs\residual-choice-patched-20260812.csv
- 不动字段：correct_answer

## 死题批量归档（有问题的题都剔除）

- 脚本：scripts/data_cleanup/20260812_archive_dead_inventory.py
- 时间：2026-08-12T12:04:48.862081+00:00
- 模式：dry-run
- 扫描：active 题 1397 道
- 命中归档：362 道
- 原因分布：{'dead_no_answer': 116, 'dead_no_type': 243, 'dead_broken_answer': 3}
- 期望核对：未设（跳过熔断）
- 备份表：questions_removed_backup_20260812_opt
- CSV：（未写出）

## 死题批量归档（有问题的题都剔除）

- 脚本：scripts/data_cleanup/20260812_archive_dead_inventory.py
- 时间：2026-08-12T12:05:23.444543+00:00
- 模式：实际执行
- 扫描：active 题 1397 道
- 命中归档：362 道
- 原因分布：{'dead_no_answer': 116, 'dead_no_type': 243, 'dead_broken_answer': 3}
- 期望核对：--expect 362
- 备份表：questions_removed_backup_20260812_opt
- CSV：D:\制作网页\cuotixiaolaoshi\docs\dead-inventory-removed-20260812.csv

## 死题批量归档（有问题的题都剔除）

- 脚本：scripts/data_cleanup/20260812_archive_dead_inventory.py
- 时间：2026-08-12T12:05:38.220419+00:00
- 模式：dry-run
- 扫描：active 题 1035 道
- 命中归档：0 道
- 原因分布：{}
- 期望核对：未设（跳过熔断）
- 备份表：questions_removed_backup_20260812_opt
- CSV：（未写出）

## 死题批量归档（有问题的题都剔除）

- 脚本：scripts/data_cleanup/20260812_archive_dead_inventory.py
- 时间：2026-08-12T14:59:59.188058+00:00
- 模式：dry-run
- 扫描：active 题 1035 道
- 命中归档：0 道
- 原因分布：{}
- 期望核对：未设（跳过熔断）
- 备份表：questions_removed_backup_20260812_opt
- CSV：（未写出）

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-12 17:04
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-12 17:04
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-12 17:04
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-12 17:16
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-12 17:16
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-12 17:16
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-12 17:30
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-12 17:30
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-12 17:30
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-13 07:13
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-13 07:13
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-13 07:13
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-13 08:59
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-13 08:59
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-13 08:59
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-13 15:47
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-13 15:47
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-13 15:47
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-13 15:51
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-13 15:51
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-13 15:51
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-13 16:01
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-13 16:01
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-13 16:01
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-13 16:16
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-13 16:16
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-13 16:17
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-13 16:17
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-13 16:17
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-13 16:17
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-13 16:25
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-13 16:25
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-13 16:25
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-13 16:36
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-13 16:36
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-13 16:36
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-15 02:59
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-15 02:59
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-15 02:59
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-18 16:15
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-18 16:15
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-18 16:15
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-18 16:19
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-18 16:19
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-18 16:19
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-19 05:54
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-19 05:54
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-19 05:54
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-19 06:19
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-19 06:19
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-19 06:19
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-19 06:21
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-19 06:21
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-19 06:21
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-19 07:22
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-19 07:22
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-19 07:22
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-19 07:26
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-19 07:26
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-19 07:26
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-19 07:53
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-19 07:53
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-19 07:54
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-19 08:28
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-19 08:28
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-19 08:28
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-19 09:59
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-19 10:00
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-19 10:00
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-20 13:26
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-20 13:26
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-20 13:26
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-20 15:48
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-20 15:48
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-20 15:48
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-20 15:50
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-20 15:50
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-20 15:50
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

## 阶段 A · 缺选项归档

- 脚本：scripts/data_cleanup/20260811_archive_incomplete_options.py
- 时间：2026-08-20 15:53
- 备份表：questions_options_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：active 选择题 5 道
- 命中归档：2 道
- 不动字段：options 内容、correct_answer

## 阶段 B · E2E 前缀剥离

- 脚本：scripts/data_cleanup/20260811_strip_e2e_prefix.py
- 时间：2026-08-20 15:53
- 备份表：questions_content_backup_20260811（已跳过）
- 模式：dry-run
- 扫描：questions 全表 5 行
- 命中剥离：2 行
- 不动字段：correct_answer、options
- 正则：^\s*E2E\s+ (re.IGNORECASE)

## 阶段 C · KP 自动打标 + 人工标注导出

- 脚本：scripts/data_cleanup/20260811_autotag_unmapped.py
- 时间：2026-08-20 15:53
- 模式：dry-run
- active 无 kp 全库：3 题
- 本轮候选扫描：3 题
- 自动打标：0 题
- 待人工标注：3 题
- 导出 CSV：（已跳过 dry-run）
- 内置关键词数：16

