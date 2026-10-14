# 全量回归 · 7 项失败测试立项跟进（2026-10-01）

> 上游：`后端全量回归`（2026-10-01）｜结果 **841 passed / 1 skipped / 7 failed**，其余 834 项与 KG-001（知识点库 V6.5 种子）零关联。
> 本文为 7 项失败分别立项（FT-001 ~ FT-007），含根因、影响、处置方向、验收口径；不属于 V6.5 Sprint 既定任务的新增清单，随 Sprint 7 / 物料补齐转绿。

## 0. 背景

| 项 | 结论 |
|---|---|
| 全量回归命令 | `pytest`（backend 根目录，含 tests/ 全量） |
| 结果 | 841 passed / 1 skipped / 7 failed |
| 失败分布 | review 模块 6 项（FT-001~006）+ docx 导入物料 1 项（FT-007） |
| 与 KG-001 关系 | 交叉引用核查确认零因果；全部为存量技术债 |
| 立项载体 | 本文档（docs/ 下独立 md，遵循 `incident-*.md` / `v41-task-breakdown.md` 表格+分节惯例） |

## 1. 立项总览

| # | 测试文件 · 用例 | 根因 | 影响面 | 处置方向 | 优先级 | 验收口径 |
|---|---|---|---|---|---|---|
| FT-001 | `test_review_service.py::test_seed_initial_idempotent` | `review_service` 缺模块级 `seed_initial()` | FT-001/002/006 同源；复习初始种子不可建 | 随 Sprint 7 方向三 FSRS 实现模块级 API | P1 | ✅ 已关闭（2026-10-01） |
| FT-002 | `test_review_service.py::test_list_due_reviews_filters_by_due_at` | 同 FT-001（依赖 `seed_initial` + 缺 `list_due_reviews()`） | 到期队列过滤逻辑无覆盖 | 同 FT-001 | P1 | ✅ 已关闭（2026-10-01） |
| FT-003 | `test_review_service.py::test_lapse_resets_repetitions_and_ef` | 缺模块级 `record_review()` | 遗忘重置（lapse）分支无覆盖 | 同 FT-001 | P1 | ✅ 已关闭（2026-10-01） |
| FT-004 | `test_review_service.py::test_record_review_creates_row_and_sets_due` | 缺模块级 `record_review()` | 复习落库 + due_at 无覆盖 | 同 FT-001 | P1 | ✅ 已关闭（2026-10-01） |
| FT-005 | `test_review_service.py::test_record_review_from_result_maps_correct_to_quality5` | 缺模块级 `record_review_from_result()` | 对错→quality 映射无覆盖 | 同 FT-001 | P1 | ✅ 已关闭（2026-10-01） |
| FT-006 | `test_review_api.py::test_review_today_and_record` | `review_service` 缺 `seed_initial`（API 层间接依赖） | 复习 API 主链路无覆盖 | 同 FT-001（API 回归随实现一并验证） | P1 | ✅ 已关闭（2026-10-01） |
| FT-007 | `test_t05_docx_import.py::test_manifest_total_source_count_is_twenty` | `D:\我的文档\Documents\数学题` 下 20 个登记物料 docx 全部缺失 | 青岛版 5 + 苏教 A 4 对 + 苏教 B 7 共 20 源文件导入链路不可测 | 补齐源物料目录；或按现状调整 manifest 登记 | P1 | 该用例转绿（`missing_files()` 为空） |

## 2. 组 A：review 模块级 API 缺失（FT-001 ~ FT-006）

### 2.1 根因

- 测试（2026-08-13 先行）按**模块级函数契约**编写：`rs.seed_initial(s, st.id, kp_ids)`、`rs.record_review(s, st.id, kp_id, quality=5, now=now)`、`rs.record_review_from_result(...)`、`rs.list_due_reviews(...)`。
- 当前实现（`app/services/review_service.py`，2026-09-21）只有 **`ReviewService` 类方法**（`create_card_for_question` / `submit_review` / `get_due_cards` / `get_review_stats` 等），没有上述模块级函数 → 收集阶段即 `AttributeError`。
- `tests/conftest.py` 明确当前是「FSRS 方向三中间态」：`fsrs_scheduler` 尚未落地，注入显式 `NotImplementedError` 占位，落地后删除该段。

```python
# tests/conftest.py L26-30
# FSRS 方向三中间态兜底：``app.services.fsrs_scheduler`` 尚未落地（方向三开发中），
# 而 ``review_service`` 模块级硬 import 它，导致任何 import app.main 的测试都无法收集。
# 这里仅在模块缺失时注入「可导入、可构造、算法显式报错」的占位实现，保证测试套件
# 整体可运行；占位不伪造任何 FSRS 算法——真正走到算法路径的调用会得到明确的
# ``NotImplementedError``。fsrs_scheduler 落地后删除本段即可。
```

```text
# 失败模式（FT-001 示例，其余同构）
tests/test_review_service.py:149: test_seed_initial_idempotent
E   AttributeError: module 'app.services.review_service' has no attribute 'seed_initial'
```

### 2.2 影响

- 复习算法（FSRS 间隔、遗忘重置、到期队列、quality 映射）**全部无自动化覆盖**，属方向三核心功能空白。
- 不阻塞 V6.5 其他 Sprint；Sprint 7 Review Engine（R-001~005：复习间隔/队列/seed）正是本组落点。

### 2.3 处置方向与验收

1. **随 Sprint 7（R-001~005）一并实现**：在 `review_service` 暴露测试契约的模块级函数（或薄包装指向类实现），`fsrs_scheduler` 落地后删除 conftest 占位段。
2. **验收口径**：`pytest tests/test_review_service.py tests/test_review_api.py` 全绿；且新实现须为真实 FSRS/SM-2 逻辑，不接受借占位绕过 `NotImplementedError`。
3. **责任归属**：Sprint 7 Review Engine 负责人，纳入 Sprint 7 完成条件清单。
4. **✅ 已关闭（2026-10-01）**：`review_service.py` 已补模块级薄包装（`seed_initial` / `record_review` / `record_review_from_result` / `list_due_reviews` / `get_state`），复用 `review_scheduler`（SM-2 纯算法层，已有 8 项单测）做转换与 upsert，未触碰 fsrs 占位。`pytest tests/test_review_service.py tests/test_review_api.py tests/test_review_scheduler.py` = **14 passed**；全量回归 **847 passed / 1 skipped / 1 failed**（残余失败仅 FT-007）。

## 3. 组 B：docx 导入物料缺失（FT-007）

### 3.1 根因

- `app/services/importers/manifest.py` 登记 **20 个真实试卷 docx**（青岛版暑假衔接 5 + 苏教 A 期末摸底诊断卷 4 对 = 8 + 苏教 B 专项复习卷 7），期望位于 `D:\我的文档\Documents\数学题`。
- 实测该目录下 **20 个文件全部缺失**（非部分缺失），`missing_files()` 返回 20 项。

```text
# 缺失文件（20/20），示例
实验班暑假衔接一升二数学青岛版h_decrypted.docx
2026年（下册）5年级 数学 苏教版 期末摸底诊断卷（一）正文(1).docx
苏教五下期末冲刺专项复习卷5套(1).docx
专项复习卷1(1).docx
```

### 3.2 影响

- docx 导入链路（解析 → 归因 → 入库）整体不可自动化验证；该文件原本只含「登记数校验」一个失败用例，其余用例被 `pytest.importorskip` 式前置挡下或同样依赖物料。

### 3.3 处置方向与验收

1. **首选（需用户/资料提供者配合）**：将 20 个原始 docx 放入 `D:\我的文档\Documents\数学题`（保持 `manifest.py` 登记文件名一致），用例即转绿。
2. **备选（物料不可得时）**：与项目 owner 确认后按实际可用物料调整 manifest 登记与 `EXPECTED_SOURCE_COUNT`（当前 20），保持"登记=磁盘实际"不变量。
3. **验收口径**：`pytest tests/test_t05_docx_import.py::test_manifest_total_source_count_is_twenty` 转绿，即 `len(ALL_SOURCES) == EXPECTED_SOURCE_COUNT` 且 `missing_files()` 为空。

## 4. 跟踪规则

| 规则 | 内容 |
|---|---|
| 状态字段 | 每项在对应 Sprint/处置完成后在「立项总览」标注 `✅ 已关闭（日期，证据）` |
| 联动 | FT-001~006 关闭 = Sprint 7 交付门禁之一；FT-007 关闭 = docx 导入回归门禁之一 |
| 回归兜底 | 全量 `pytest` 当前 **847 passed / 1 skipped / 1 failed**（FT-001~006 已关闭转绿，仅剩 FT-007）；FT-007 关闭后目标 **848 passed / 1 skipped / 0 failed** |
| 不与既有规划冲突 | 本文档不修改 `错题教练-V6.5开发总规划-20261001.docx` 的 Sprint/Task 结构，只作为执行层失败项跟踪载体 |