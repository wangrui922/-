# -*- coding: utf-8 -*-
"""生活场景题（``source_doc='scenario_bank'``）→ 知识点（KP）的静态映射表。

背景
----
``backend/scripts/generate_scenario_bank.py`` 写入场景题时把 ``kp_id`` 硬编码成
``None``（该脚本 INSERT 语句），于是 864 道场景题全部落在知识点体系之外。

后果**不是**"题库少 864 道题"，而是**归因断链**：

* ``app/services/student_dna_service.py`` 的 ``_build_six_dim_events`` 与
  ``_build_retention_events`` 在 ``kp_id is None`` 时直接 ``continue``；
  于是学生做过这些题，也不会产生任何带 ``knowledge_point_id`` 的 L1 事件；
* L1 → L2 → L3 学情链路随之断开——六维画像 / 遗忘曲线 / 掌握度更新都拿不到
  这批题的数据。

本模块是 B-0「存量回填」的**规则来源**：把 24 个 ``scenario_template`` 子模板
映射到 7 个已存在的知识点 ``code`` 上。

设计约束（engines 层铁律）
-------------------------
* **纯函数层**：只依赖标准库；禁止 import ``sqlalchemy`` / ``Session`` /
  ``os.environ`` / 配置文件；
* **按 code 映射，不写死 uuid**：``kp_id`` 在运行时按 ``code`` 查库解析，
  表结构或 id 变化不会让本模块失效；
* **不反推题干**：映射依据是**题目生成模板本身的数学结构**
  （见 ``generate_scenario_bank.py`` 的 ``t_shopping`` / ``t_cake`` / ``t_time`` /
  ``t_distance_dual``），不是题干关键词——实测 328 个知识点名称在这 864 道题的
  题干里命中 **0** 次，关键词反推不可行；
* **不按 grade 拆分**：场景题的 ``grade`` 标签偏高（同一难度下 g3 / g5 的算式
  复杂度基本一致），按年级拆会把同一个数学概念拆到 6 个知识点上，制造伪差异化。

粒度说明：``scenario_template`` 序号与模板内部 ``kind`` 存在偏移
-------------------------------------------------------------
``generate_scenario_bank.py::build_items`` 里：

    content, ans, dis = gen(cell, rng)        # 模板内部 kind = cell % 6
    ...
    cell += 1
    "scenario_template": f"{scene}/{cell % 6}",   # 落库索引 = (kind + 1) % 6

即落库的 ``scenario_template`` 序号比模板内部 ``kind`` **大 1（模 6）**。
本表每一行都已按该偏移逐条核对过实际题干（核对样本见
``backend/tests/test_backfill_scenario_kp.py``）。

关于 ``kp_verified``：**本轮不需要动它**
----------------------------------------
864 道场景题的 ``kp_verified`` **本来就是 1**——``generate_scenario_bank.py``
写 INSERT 时同时写了 ``kp_verified=1``（配套还有 ``source_tag='green'``、
``ship_ready=1``、``status='active'``）。所以 B-0 回填**只需要写
``questions.kp_id`` 一列**：

* 回填脚本的唯一写操作是
  ``UPDATE questions SET kp_id = ? WHERE id = ? AND kp_id IS NULL``；
* ``kp_verified`` **既不需要同步改，也不应该改**——它已经是"人工已复核"的
  语义值，改它等于凭空制造写入；
* "只改一列"是本轮回填的硬不变式，脚本与它的守卫测试都按**逐列比对**
  验证这一点（备份 vs 回填后：变化列集合必须恰好等于 ``{"kp_id"}``）。

⚠️ 后人若看到"回填"就顺手把 ``kp_verified`` 也刷一遍，那是**多余的写入**，
而且会直接破坏上面那条不变式——`test_only_kp_id_column_changes` 会变红。

审计
----
``export_mapping_json()`` 输出可直接归档的 JSON（含每条的 ``reason``），
供人工过目；``resolve_kp_codes()`` 是纯函数校验入口。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple

__all__ = [
    "ScenarioKPRecord",
    "SCENARIO_KP_RECORDS",
    "SCENARIO_KP_MAPPING",
    "MAPPING_REASONS",
    "TARGET_KP_CODES",
    "EXPECTED_QUESTION_COUNT",
    "ANNOTATION_ISSUES",
    "export_mapping_json",
    "resolve_kp_codes",
    "resolve_kp_code",
    "validate_mapping_table",
]


# ============================================================ 数据类
@dataclass(frozen=True)
class ScenarioKPRecord:
    """一条「场景子模板 → 知识点」映射记录。

    Attributes:
        scenario_template: 场景子模板键，形如 ``"cake/0"``（共 24 条）。
        kp_code: 目标知识点的 ``code``（**不是 uuid**；uuid 运行时按 code 查）。
        math_operation: 该子模板的数学操作（中文一句话）。
        reason: **为什么**归到这个 KP（中文一句话，供人工过目）。
        expected_question_count: 该子模板在存量库中的题目数（实测值，
            合计 864）。
    """

    scenario_template: str
    kp_code: str
    math_operation: str
    reason: str
    expected_question_count: int

    def to_dict(self) -> Dict[str, Any]:
        """转成可 JSON 序列化的 dict（键名保持 snake_case，便于审计 diff）。"""
        return {
            "scenario_template": self.scenario_template,
            "kp_code": self.kp_code,
            "math_operation": self.math_operation,
            "reason": self.reason,
            "expected_question_count": int(self.expected_question_count),
        }


# ============================================================ 映射表（24 条）
#: 24 个 ``scenario_template`` 子模板 → 知识点 code 的映射。
#:
#: 口径说明（按 team 决议，勿轻易改）：
#:   * ``distance`` 全族（216 题）归 ``KP_WORD_TRAVEL``——题库里**只有这一条**
#:     行程类知识点（g3 / g4 无行程 KP），拆不开；
#:   * ``cake`` / ``shopping`` / ``time`` 各按**算式结构**落到加减乘除与
#:     时间单位知识点，不按 grade 拆分。
SCENARIO_KP_RECORDS: Tuple[ScenarioKPRecord, ...] = (
    # ---------------- cake 分蛋糕（216 题）----------------
    ScenarioKPRecord(
        scenario_template="cake/0",
        kp_code="KP_CALC_DIV_1D_VERIFY",
        math_operation="有余数平均分（最多分几块）",
        reason="总数除以人数取整并看余数，属有余数除法，归入「除法验算与有余数」。",
        expected_question_count=41,
    ),
    ScenarioKPRecord(
        scenario_template="cake/1",
        kp_code="KP_CALC_DIV_1D",
        math_operation="平均分求每份",
        reason="总块数÷人数=每份数，除数为一位数且可整除，归入「除数是一位数的除法」。",
        expected_question_count=34,
    ),
    ScenarioKPRecord(
        scenario_template="cake/2",
        kp_code="KP_CALC_DIV_1D",
        math_operation="平均分求每份",
        reason="总个数÷份数=每份数，除数为一位数的平均分，归入「除数是一位数的除法」。",
        expected_question_count=38,
    ),
    ScenarioKPRecord(
        scenario_template="cake/3",
        kp_code="KP_CALC_MUL",
        math_operation="每份×份数=总数",
        reason="每人块数×人数=总块数，是乘法模型，归入「乘除法」。",
        expected_question_count=31,
    ),
    ScenarioKPRecord(
        scenario_template="cake/4",
        kp_code="KP_CALC_DIV_1D_VERIFY",
        math_operation="平均分后求余数",
        reason="先按人数平均分再求剩余，核心是商余结构，归入「除法验算与有余数」。",
        expected_question_count=46,
    ),
    ScenarioKPRecord(
        scenario_template="cake/5",
        kp_code="KP_CALC_MUL",
        math_operation="每块×蛋糕数=总块数",
        reason="每蛋糕块数×蛋糕个数=总块数，是乘法模型，归入「乘除法」。",
        expected_question_count=26,
    ),
    # ---------------- distance 路程计算（216 题）----------------
    ScenarioKPRecord(
        scenario_template="distance/0",
        kp_code="KP_WORD_TRAVEL",
        math_operation="总路程÷时间=速度",
        reason="总路程÷用时=速度，是行程问题基本型；题库中行程类知识点仅此一条，故整族归入。",
        expected_question_count=32,
    ),
    ScenarioKPRecord(
        scenario_template="distance/1",
        kp_code="KP_WORD_TRAVEL",
        math_operation="速度×时间=路程",
        reason="速度×时间=路程，是行程问题基本型；题库中行程类知识点仅此一条，故整族归入。",
        expected_question_count=37,
    ),
    ScenarioKPRecord(
        scenario_template="distance/2",
        kp_code="KP_WORD_TRAVEL",
        math_operation="速度×时间=路程",
        reason="速度×时间=路程，是行程问题基本型；题库中行程类知识点仅此一条，故整族归入。",
        expected_question_count=36,
    ),
    ScenarioKPRecord(
        scenario_template="distance/3",
        kp_code="KP_WORD_TRAVEL",
        math_operation="路程÷速度=时间",
        reason="路程÷速度=用时，是行程问题基本型；题库中行程类知识点仅此一条，故整族归入。",
        expected_question_count=33,
    ),
    ScenarioKPRecord(
        scenario_template="distance/4",
        kp_code="KP_WORD_TRAVEL",
        math_operation="路程÷速度=时间",
        reason="路程÷速度=用时，是行程问题基本型；题库中行程类知识点仅此一条，故整族归入。",
        expected_question_count=40,
    ),
    ScenarioKPRecord(
        scenario_template="distance/5",
        kp_code="KP_WORD_TRAVEL",
        math_operation="总路程÷时间=速度",
        reason="总路程÷用时=速度，是行程问题基本型；题库中行程类知识点仅此一条，故整族归入。",
        expected_question_count=38,
    ),
    # ---------------- shopping 购物算账（216 题）----------------
    ScenarioKPRecord(
        scenario_template="shopping/0",
        kp_code="KP_CALC_MUL",
        math_operation="找零：付款−总价",
        reason="先由单价×件数得总价，再用付款−总价求找零，乘法是必经步骤，归入「乘除法」。",
        expected_question_count=34,
    ),
    ScenarioKPRecord(
        scenario_template="shopping/1",
        kp_code="KP_CALC_MUL",
        math_operation="剩余：付款−总价",
        reason="先由单价×件数得总价，再用付款−总价求剩余，乘法是必经步骤，归入「乘除法」。",
        expected_question_count=42,
    ),
    ScenarioKPRecord(
        scenario_template="shopping/2",
        kp_code="KP_CALC_MUL",
        math_operation="a×x+b×y 总价",
        reason="两种商品各自单价×数量再相加，乘法模型，归入「乘除法」。",
        expected_question_count=38,
    ),
    ScenarioKPRecord(
        scenario_template="shopping/3",
        kp_code="KP_CALC_DIV_1D",
        math_operation="总价÷数量=单价",
        reason="总价÷数量=单价，除数为一位数的除法，归入「除数是一位数的除法」。",
        expected_question_count=30,
    ),
    ScenarioKPRecord(
        scenario_template="shopping/4",
        kp_code="KP_CALC_MUL",
        math_operation="a×x+b×y 总价",
        reason="两种商品单价×数量后相加，乘法模型，归入「乘除法」。",
        expected_question_count=36,
    ),
    ScenarioKPRecord(
        scenario_template="shopping/5",
        kp_code="KP_CALC_ADD",
        math_operation="满减：原价−立减",
        reason="满减后只做原价−立减这一步减法，归入「加减法」。",
        expected_question_count=36,
    ),
    # ---------------- time 时间规划（216 题）----------------
    ScenarioKPRecord(
        scenario_template="time/0",
        kp_code="KP_UNIT_TIME_ELAPSE",
        math_operation="经过时间（时刻差）",
        reason="由两个时刻相减求经过时长，属经过时间计算，归入「经过时间计算」。",
        expected_question_count=50,
    ),
    ScenarioKPRecord(
        scenario_template="time/1",
        kp_code="KP_UNIT_TIME_ELAPSE",
        math_operation="时刻+经过时间=结束时刻",
        reason="起始时刻加经过时长得到结束时刻，属经过时间计算，归入「经过时间计算」。",
        expected_question_count=51,
    ),
    ScenarioKPRecord(
        scenario_template="time/2",
        kp_code="KP_UNIT_TIME_ELAPSE",
        math_operation="时刻+经过时间=到达时刻",
        reason="出发时刻加路上时长得到到达时刻，属经过时间计算，归入「经过时间计算」。",
        expected_question_count=50,
    ),
    ScenarioKPRecord(
        scenario_template="time/3",
        kp_code="KP_CALC_MUL",
        math_operation="时长×份数=总时长",
        reason="每节课时长×节数=总时长，是乘法模型，归入「乘除法」。",
        expected_question_count=8,
    ),
    ScenarioKPRecord(
        scenario_template="time/4",
        kp_code="KP_UNIT_TIME",
        math_operation="时→分 单位换算",
        reason="小时换算成分钟属时间单位换算，归入「时间单位」。",
        expected_question_count=8,
    ),
    ScenarioKPRecord(
        scenario_template="time/5",
        kp_code="KP_CALC_ADD",
        math_operation="总时长−已用=还剩",
        reason="总时长减已用时长得剩余时间，是减法模型，归入「加减法」。",
        expected_question_count=49,
    ),
)


# ============================================================ 便捷视图
#: ``scenario_template`` → ``kp_code``（24 条）。
SCENARIO_KP_MAPPING: Dict[str, str] = {
    record.scenario_template: record.kp_code for record in SCENARIO_KP_RECORDS
}

#: ``scenario_template`` → 归因理由（中文一句话）。
MAPPING_REASONS: Dict[str, str] = {
    record.scenario_template: record.reason for record in SCENARIO_KP_RECORDS
}

#: 映射表涉及的全部知识点 code（7 条，去重且有序）。
TARGET_KP_CODES: Tuple[str, ...] = tuple(
    sorted({record.kp_code for record in SCENARIO_KP_RECORDS})
)

#: 映射表覆盖的题目总数（实测 864，与存量场景题总数一致）。
EXPECTED_QUESTION_COUNT: int = sum(
    record.expected_question_count for record in SCENARIO_KP_RECORDS
)


# ============================================================ 标注问题登记
#: 本轮**不回改**任何题的 ``grade`` 或其它字段；以下问题仅登记到回填报告的
#: ``annotation_issue`` 段，供后续人工治理批次处理。
ANNOTATION_ISSUES: Tuple[Dict[str, Any], ...] = (
    {
        "issue_id": "distance_grade_vs_kp_grade",
        "severity": "medium",
        "scope": "distance/0~distance/5（216 题）",
        "summary": "distance 全族归入 g5 的 KP_WORD_TRAVEL，但其中含 g3/g4 标签题目。",
        "detail": (
            "题库中速度/路程类知识点只有 KP_WORD_TRAVEL 一条（g5），g3/g4 没有对应 KP，"
            "因此 216 道 distance 题（含 g3 标签、算式仅为「速度×时间」的题）统一归到 g5 的 KP。"
            "这会形成「低年级标签 + 高年级知识点」的张力：按 KP 聚合出的掌握度画像会把 g3 学生的"
            "行程题计入 g5 知识点。"
        ),
        "action": "本轮不改 grade；仅登记。后续可考虑新增 g3 行程 KP 或为 distance 场景独立标记。",
    },
    {
        "issue_id": "distance_template_mixed_operation",
        "severity": "low",
        "scope": "distance/0~distance/5",
        "summary": "distance 场景由 t_distance / t_distance2 双模板按 cell 奇偶交替生成，"
        "同一 scenario_template 下混有两种算式。",
        "detail": (
            "generate_scenario_bank.py::t_distance_dual 以 cell 奇偶切换两套模板，"
            "而落库索引为 (kind+1)%6，导致同一子模板下既有「速度×时间」也有「千米/米单位换算」"
            "「两段路程相加」等变体。本轮 distance 全族 KP 相同，故不影响归因结果。"
        ),
        "action": "本轮不影响归因；仅登记，供后续细化子模板粒度时参考。",
    },
    {
        "issue_id": "shopping_subtraction_inconsistent_kp",
        "severity": "low",
        "scope": "shopping/0、shopping/1（76 题）vs shopping/5（36 题）",
        "summary": "同为「付款/原价 − 总价」的减法型模板，归到了不同的 KP。",
        "detail": (
            "shopping/0、shopping/1 需要先算「单价×件数」再相减，映射到乘法侧 KP_CALC_MUL；"
            "shopping/5 满减只做一次减法，映射到 KP_CALC_ADD。同一「减法」语义落在两个 KP 上，"
            "聚合口径上会让「加减法」与「乘除法」各持一部分找零/剩余题。"
        ),
        "action": "保持现状（映射按 team 决议定稿）；仅登记，供人工复核时判断是否统一。",
    },
    {
        "issue_id": "kp_name_broader_than_operation",
        "severity": "info",
        "scope": "KP_CALC_MUL（546 题）/ KP_CALC_ADD（505 题）",
        "summary": "KP 名称口径宽于本轮映射的算式粒度。",
        "detail": (
            "KP_CALC_MUL 名为「乘除法」、KP_CALC_ADD 名为「加减法」，实际覆盖面远大于"
            "本轮映射到的乘法 / 加减法算式，因此归因粒度粗于模板粒度——这是知识点表本身的"
            "粒度问题，不是回填引入的。"
        ),
        "action": "仅登记；不修改知识点表（超本轮范围）。",
    },
)


# ============================================================ 纯函数接口
def export_mapping_json() -> Dict[str, Any]:
    """导出可直接归档的映射表 JSON。

    Returns:
        含 ``records``（24 条映射记录）、``kp_codes``、``template_count``、
        ``expected_question_count`` 与 ``annotation_issue`` 的 dict；
        其中 ``records`` 按 ``scenario_template`` 升序排列，保证导出稳定可比对。
    """
    records = sorted(
        (record.to_dict() for record in SCENARIO_KP_RECORDS),
        key=lambda item: item["scenario_template"],
    )
    return {
        "version": "B-0",
        "source_doc": "scenario_bank",
        "template_count": len(records),
        "kp_code_count": len(TARGET_KP_CODES),
        "expected_question_count": EXPECTED_QUESTION_COUNT,
        "kp_codes": list(TARGET_KP_CODES),
        "records": records,
        "annotation_issue": [dict(issue) for issue in ANNOTATION_ISSUES],
    }


def resolve_kp_code(scenario_template: str) -> str:
    """按子模板取目标知识点 code。

    Args:
        scenario_template: 子模板键，如 ``"cake/0"``。

    Returns:
        目标知识点 code。

    Raises:
        KeyError: 映射表里没有这个子模板（**不容忍静默跳过**——回填脚本靠这个
            异常终止整批，而不是悄悄漏题）。
    """
    key = str(scenario_template).strip()
    if key not in SCENARIO_KP_MAPPING:
        raise KeyError(f"映射表未覆盖场景子模板: {scenario_template!r}")
    return SCENARIO_KP_MAPPING[key]


@dataclass(frozen=True)
class CodeResolution:
    """一个知识点 code 在映射表里的解析结果（纯数据）。

    Attributes:
        code: 知识点 code。
        known: 该 code 是否出现在映射表中。
        templates: 映射到该 code 的子模板（升序）。
        expected_question_count: 这些子模板合计应有的题目数。
        math_operations: 这些子模板的数学操作描述（升序）。
    """

    code: str
    known: bool
    templates: Tuple[str, ...]
    expected_question_count: int
    math_operations: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        """转成可 JSON 序列化的 dict。"""
        return {
            "code": self.code,
            "known": bool(self.known),
            "templates": list(self.templates),
            "expected_question_count": int(self.expected_question_count),
            "math_operations": list(self.math_operations),
        }


def resolve_kp_codes(codes: Iterable[str]) -> Tuple[CodeResolution, ...]:
    """纯函数校验入口：把知识点 code 列表解析成「覆盖哪些模板 / 应回填多少题」。

    这是**映射表自身**的一致性校验入口（不碰数据库）：调用方（例如回填脚本）
    可以先拿本函数确认待用的 code 全都在映射表内，再去库里查 id 与 status。

    Args:
        codes: 待解析的知识点 code 序列（允许含未知 code / 重复项）。

    Returns:
        与入参**一一对应**（保序、保留重复）的 ``CodeResolution`` 元组；
        未知 code 的 ``known`` 为 ``False``，``templates`` 为空。
    """
    index: Dict[str, List[ScenarioKPRecord]] = {}
    for record in SCENARIO_KP_RECORDS:
        index.setdefault(record.kp_code, []).append(record)

    results: List[CodeResolution] = []
    for raw in codes:
        code = str(raw).strip()
        matched = index.get(code, [])
        templates = tuple(sorted(item.scenario_template for item in matched))
        operations = tuple(
            sorted({item.math_operation for item in matched})
        )
        results.append(
            CodeResolution(
                code=code,
                known=bool(matched),
                templates=templates,
                expected_question_count=sum(
                    item.expected_question_count for item in matched
                ),
                math_operations=operations,
            )
        )
    return tuple(results)


def validate_mapping_table() -> Dict[str, Any]:
    """校验映射表自身的完整性，返回结构化结论（不抛异常）。

    检查项：

        * 记录数是否恰好 24；
        * ``scenario_template`` 是否无重复；
        * 是否覆盖了 4 个场景 × 6 个子模板（24 个键的**全集**）；
        * 每条是否都带非空 ``reason``；
        * ``expected_question_count`` 合计是否为 864；
        * 是否存在没有题目的"空"子模板（映射覆盖率）。

    Returns:
        ``{"ok": bool, "issues": [...], "stats": {...}}``。
    """
    issues: List[str] = []
    templates = [record.scenario_template for record in SCENARIO_KP_RECORDS]

    if len(SCENARIO_KP_RECORDS) != 24:
        issues.append(f"映射记录数应为 24，实际 {len(SCENARIO_KP_RECORDS)}")

    duplicates = sorted({tpl for tpl in templates if templates.count(tpl) > 1})
    if duplicates:
        issues.append(f"scenario_template 重复: {duplicates}")

    expected_keys = {
        f"{scene}/{index}"
        for scene in ("cake", "distance", "shopping", "time")
        for index in range(6)
    }
    missing = sorted(expected_keys - set(templates))
    if missing:
        issues.append(f"缺失的 scenario_template: {missing}")
    extra = sorted(set(templates) - expected_keys)
    if extra:
        issues.append(f"多余的 scenario_template: {extra}")

    empty_reasons = [
        record.scenario_template
        for record in SCENARIO_KP_RECORDS
        if not str(record.reason).strip()
    ]
    if empty_reasons:
        issues.append(f"缺少 reason 的记录: {empty_reasons}")

    if EXPECTED_QUESTION_COUNT != 864:
        issues.append(
            f"expected_question_count 合计应为 864，实际 {EXPECTED_QUESTION_COUNT}"
        )

    empty_cells = [
        record.scenario_template
        for record in SCENARIO_KP_RECORDS
        if int(record.expected_question_count) <= 0
    ]
    if empty_cells:
        issues.append(f"存在零题量的子模板（空格子）: {empty_cells}")

    return {
        "ok": not issues,
        "issues": issues,
        "stats": {
            "record_count": len(SCENARIO_KP_RECORDS),
            "template_count": len(set(templates)),
            "kp_code_count": len(TARGET_KP_CODES),
            "expected_question_count": EXPECTED_QUESTION_COUNT,
            "empty_cells": empty_cells,
            "coverage": (
                1.0
                if not empty_cells
                else round(
                    1.0 - len(empty_cells) / max(1, len(SCENARIO_KP_RECORDS)), 4
                )
            ),
        },
    }
