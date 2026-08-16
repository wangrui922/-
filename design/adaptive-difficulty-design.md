# 自适应难度与智能层 · 落地设计（基于真实代码）

> 写于 2026-08-16。本文不是从零设计，而是**对照现有代码定位缺口**，避免重复造轮子。
> 审计中确认：后端已具备完整的「掌握度引擎」，升级重点是**接线**与**前端体验**，不是重造。

## 1. 现状盘点（已存在，可直接复用）

| 能力 | 位置 | 状态 |
|---|---|---|
| 掌握度算法（贝叶斯平滑 + 时效衰减 → 0~1 分值 / 0-3 等级） | `app/services/mastery_engine.py::compute_mastery` | ✅ 已有，纯函数可单测 |
| 掌握度物化表 | `app/models/student_mastery.py`（`mastery_score` / `mastery_level` 0未入门/1薄弱/2基本/3熟练） | ✅ 已有 |
| 掌握度查询 API | `app/api/profile.py::GET /api/profile/mastery` + `profile_service.get_mastery` | ✅ 已有 |
| 错因五类机器码 | `Mistake.error_type`（审题/方法/计算/概念/其他） | ✅ 已有 |
| 薄弱画像 | `ai_exercise_service.get_weak_profile`（按错因计数降序取主导） | ✅ 已有 |
| 复习调度 | `app/services/review_scheduler.py`（基于掌握度推复习时间） | ✅ 已有 |
| 报告/雷达数据 | `report_service` 已聚合 `mastery` + `weak_points` TopN | ✅ 已有 |
| AI 按错因生成练习 | `app/ai/agents/exercise.py::gen_exercises(error_type, kp, difficulty)` | ✅ 已有 |
| 抽题 6 档 fallback | `practice_service.draw_questions` + `_draw_with_relaxation` | ✅ 已有 |

**结论**：P1 智能层 80% 已在位。我们不用重写，只做"把已有掌握度接到抽题难度"+"把错题变形生成接进薄弱练习"。

## 2. 缺口 A：自适应难度（接线，非新算法）

**现象**：`draw_questions` 的 `difficulty` 参数由**调用方**传入（1/3/5），目前前端/接口并没有根据孩子掌握度自动选难度。孩子若总被塞难度 3，会的无聊、不会的劝退。

**方案**：新增 `draw_adaptive(db, *, student_id, kp_id, count)`，内部：
1. 查 `student_mastery` 该 kp 的 `mastery_level`；
2. 映射：`level 0/1 → difficulty 1`（简单，先建立信心）、`level 2 → difficulty 3`（中等）、`level 3 → difficulty 5`（挑战）；
3. 无记录（新知识点）→ 默认 `difficulty 1`，首题建立正反馈；
4. 调用既有 `draw_questions(difficulty=...)`。

映射表放在 `app/core/constants.py`（与 mastery 参数同处，便于调参）。

**验收**：单测覆盖"level→difficulty 映射"+"新 kp 默认简单"+"绘制后仍走既有 fallback"。

## 3. 缺口 B：错题变形题接入薄弱练习

`backend/scripts/generate_variants.py` 已验证可用（本机 DeepSeek，零额外成本）。
**接线点**：在 `ai_exercise_service` 的薄弱练习流程里，对 `get_weak_profile` 返回的主导 `error_type` + `kp`，调用变形生成，产出"同薄弱点、异情境"的题组，避免孩子刷到高度雷同的原题。

**验收**：给定一道错题 → 产出 ≥3 道变形题 JSON（stem/options/answer/explain），字段对齐 `Question` 模型。

## 4. 缺口 C：讲解质量闸门默认开启

审计发现 `explanation_quality_gate_enabled=False`（默认关）。
**动作**：改为 `True`（一行配置）。开启后每道讲解先过 `explanation_quality` 闸门再下发，保证"高级感"底线。需先在测试环境跑一轮，确认不误杀正常讲解（阈值在 `explanation_quality.py` 可调）。

## 5. 缺口 D：学情雷达图（家长/老师端）

`report_service` 已有 `mastery` 聚合。补一个轻量端点 `GET /api/profile/radar`，按**知识点维度**（如 数与代数/图形与几何/统计与概率）聚合 `mastery_level`，输出雷达图所需 5~6 维数据。前端星图即由 `mastery_level` 驱动（原型已演示）。

## 6. 优先级（修正后）

| 优先级 | 项 | 工作量 | 说明 |
|---|---|---|---|
| **P0 体验** | 前端：AI小老师IP + 情感化反馈 + 游戏化星图 | 中 | 最大兴趣杠杆，原型已出 |
| **P0 接线** | 缺口 A 自适应难度 | 小 | 复用 mastery，半天内可完成 |
| **P1 信任** | 缺口 C 质量闸门开启 | 极小 | 一行配置 + 一轮验证 |
| **P1 智能** | 缺口 B 变形题接入 | 小 | 脚本已验证，接服务层 |
| **P1 家长** | 缺口 D 学情雷达图 | 小 | 聚合已有 mastery |

> 注意：本机无外网，后端需在有网服务器部署后，AI 类（B）才真实可用；A/C/D 不依赖外网，纯算法/数据。
