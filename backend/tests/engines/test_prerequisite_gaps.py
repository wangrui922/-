# -*- coding: utf-8 -*-
"""``mastery_engine.detect_prerequisite_gaps`` 的独立单测（T07-S2 首个使用者买单）。

背景：这个函数此前**全仓零调用**，T07 是它的第一个真实使用者。按施工图要求，
接线前先补 3 条独立单测钉死语义，尤其是极易踩错的那条——
**后置知识点零样本时不判缺口**（否则整张图谱会被全标红，推荐彻底失真）。

覆盖：
    1. 后置零样本 → 不判缺口（无论前置多差）；
    2. 后置有样本 + 前置未达标 → 判缺口，且 ``missing`` 标出「前置零样本」；
    3. 前置达标 → 不判缺口；缺失的前置行按 0 分处理；
    4. 排序口径：``weight`` 降序 → ``pre_score`` 升序 → ``kp_id`` 升序；
    5. 脏入参兜底不抛异常。
"""

from __future__ import annotations

from typing import List

from app.core.constants import MASTERY_GAP_THRESHOLD
from app.services.mastery_engine import (
    MasteryResult,
    PrerequisiteEdge,
    PrerequisiteGap,
    detect_prerequisite_gaps,
)

#: 后置知识点 ID
_TARGET: str = "kp-target"
#: 前置知识点 ID
_PRE: str = "kp-pre"


def _result(score: float = 0.0, attempts: int = 1, level: int = 0) -> MasteryResult:
    """构造一个掌握度结果；``attempts`` 是「有没有练过」的唯一判据。"""
    return MasteryResult(
        score=score,
        level=level,
        attempts=attempts,
        correct_count=attempts if score >= 1.0 else 0,
        error_rate=0.0 if score >= 1.0 else 1.0,
        effective_attempts=float(attempts),
    )


def _edges_of(gaps: List[PrerequisiteGap]) -> List[tuple]:
    """把缺口列表压成 ``(kp_id, pre_kp_id)`` 便于断言。"""
    return [(gap.kp_id, gap.pre_kp_id) for gap in gaps]


# ============================================================ 三条核心语义
def test_no_gap_when_target_has_zero_samples():
    """**易错语义**：后置知识点零样本（还没练过）→ 一律不判缺口。

    前置再差也不判：整张图谱里「还没学到的章节」不应该被标红，
    否则学生一进来就看到几十条「缺口」，推荐彻底失去排序能力。
    """
    mastery = {
        _TARGET: _result(score=0.9, attempts=0),  # 后置：练过 0 次
        _PRE: _result(score=0.0, attempts=0),  # 前置：也没练过
    }
    edges = [PrerequisiteEdge(pre_kp_id=_PRE, kp_id=_TARGET, weight=1.0)]
    assert detect_prerequisite_gaps(mastery, edges) == []

    # 后置零样本 + 前置很差（有样本）：同样不判
    mastery[_PRE] = _result(score=0.1, attempts=5)
    assert detect_prerequisite_gaps(mastery, edges) == []


def test_gap_detected_when_pre_below_threshold():
    """后置有样本 + 前置掌握度 < 缺口线 → 判缺口，``missing`` 标出前置零样本。"""
    # ① 前置完全没练过 → missing=True
    mastery = {
        _TARGET: _result(score=0.8, attempts=6),
        # 注意： mastery 里**没有** _PRE 这一行（前置零样本）
    }
    edges = [PrerequisiteEdge(pre_kp_id=_PRE, kp_id=_TARGET, weight=0.9)]
    gaps = detect_prerequisite_gaps(mastery, edges)
    assert _edges_of(gaps) == [(_TARGET, _PRE)]
    assert gaps[0].missing is True
    assert gaps[0].pre_score == 0.0
    assert gaps[0].pre_level == 0
    assert gaps[0].kp_score == 0.8
    assert gaps[0].weight == 0.9

    # ② 前置练过但没达标 → missing=False，pre_score 如实回填
    mastery[_PRE] = _result(score=MASTERY_GAP_THRESHOLD - 0.1, attempts=4, level=1)
    gaps = detect_prerequisite_gaps(mastery, edges)
    assert _edges_of(gaps) == [(_TARGET, _PRE)]
    assert gaps[0].missing is False
    assert gaps[0].pre_score == MASTERY_GAP_THRESHOLD - 0.1
    assert gaps[0].pre_level == 1


def test_no_gap_when_pre_meets_threshold():
    """前置掌握度 >= 缺口线 → 依赖已满足，不判缺口（含恰好等于的边界）。"""
    edges = [PrerequisiteEdge(pre_kp_id=_PRE, kp_id=_TARGET, weight=1.0)]

    # 恰好等于阈值：用 ``>=`` 判定，不算缺口
    mastery = {
        _TARGET: _result(score=0.5, attempts=3),
        _PRE: _result(score=MASTERY_GAP_THRESHOLD, attempts=3),
    }
    assert detect_prerequisite_gaps(mastery, edges) == []

    # 超标
    mastery[_PRE] = _result(score=0.95, attempts=8)
    assert detect_prerequisite_gaps(mastery, edges) == []


# ============================================================ 排序口径
def test_gaps_sorted_by_weight_then_pre_score_then_kp_id():
    """排序：``weight`` 降序 → ``pre_score`` 升序 → ``kp_id`` 升序（稳定可复现）。"""
    mastery = {
        "t1": _result(score=0.7, attempts=4),
        "t2": _result(score=0.7, attempts=4),
        "t3": _result(score=0.7, attempts=4),
        "p_weak": _result(score=0.1, attempts=4),
        "p_mid": _result(score=0.3, attempts=4),
    }
    edges = [
        PrerequisiteEdge(pre_kp_id="p_mid", kp_id="t1", weight=0.5),  # 弱依赖
        PrerequisiteEdge(pre_kp_id="p_weak", kp_id="t2", weight=0.9),  # 强依赖 + 前置更差
        PrerequisiteEdge(pre_kp_id="p_mid", kp_id="t3", weight=0.9),  # 强依赖 + 前置较好
    ]
    gaps = detect_prerequisite_gaps(mastery, edges)
    # 强依赖优先（t2/t3 在 t1 前）；同为 0.9 时 pre_score 升序 → t2(0.1) 先于 t3(0.3)
    assert _edges_of(gaps) == [("t2", "p_weak"), ("t3", "p_mid"), ("t1", "p_mid")]


def test_gaps_sort_is_deterministic_on_ties():
    """权重与前置分完全相同时按 ``kp_id`` 升序兜底（跨进程可复现）。"""
    mastery = {
        "b": _result(score=0.6, attempts=2),
        "a": _result(score=0.6, attempts=2),
        "p": _result(score=0.2, attempts=2),
    }
    edges = [
        PrerequisiteEdge(pre_kp_id="p", kp_id="b", weight=0.7),
        PrerequisiteEdge(pre_kp_id="p", kp_id="a", weight=0.7),
    ]
    assert _edges_of(detect_prerequisite_gaps(mastery, edges)) == [("a", "p"), ("b", "p")]
    assert _edges_of(detect_prerequisite_gaps(mastery, edges)) == [
        ("a", "p"),
        ("b", "p"),
    ]  # 幂等


# ============================================================ 兜底与边界
def test_empty_inputs_return_empty_list():
    """空掌握度表 / 空依赖边 → 空列表，不抛异常。"""
    assert detect_prerequisite_gaps({}, []) == []
    assert detect_prerequisite_gaps({}, [PrerequisiteEdge("p", "t")]) == []
    assert detect_prerequisite_gaps({"t": _result(attempts=3)}, []) == []
    assert detect_prerequisite_gaps({}, ()) == []


def test_custom_threshold_is_honored():
    """``threshold`` 可调：调高后原本达标的边会被判成缺口。"""
    mastery = {
        _TARGET: _result(score=0.5, attempts=3),
        _PRE: _result(score=0.6, attempts=3),
    }
    edges = [PrerequisiteEdge(pre_kp_id=_PRE, kp_id=_TARGET, weight=1.0)]
    assert detect_prerequisite_gaps(mastery, edges) == []  # 默认 0.5：达标
    assert _edges_of(detect_prerequisite_gaps(mastery, edges, threshold=0.8)) == [
        (_TARGET, _PRE)
    ]


def test_multiple_edges_for_one_target():
    """一个后置知识点可以有多条前置缺口（逐条返回，不合并）。"""
    mastery = {
        _TARGET: _result(score=0.7, attempts=5),
        "p1": _result(score=0.1, attempts=3),
        "p2": _result(score=0.2, attempts=3),
        "p3": _result(score=0.9, attempts=3),  # 达标，不该出现
    }
    edges = [
        PrerequisiteEdge(pre_kp_id="p1", kp_id=_TARGET, weight=1.0),
        PrerequisiteEdge(pre_kp_id="p2", kp_id=_TARGET, weight=1.0),
        PrerequisiteEdge(pre_kp_id="p3", kp_id=_TARGET, weight=1.0),
        PrerequisiteEdge(pre_kp_id="missing", kp_id=_TARGET, weight=1.0),
    ]
    gaps = detect_prerequisite_gaps(mastery, edges)
    # p1/p2/missing 都是缺口（missing 前置分 0 排最前），p3 达标被排除
    assert _edges_of(gaps) == [
        (_TARGET, "missing"),
        (_TARGET, "p1"),
        (_TARGET, "p2"),
    ]
    assert all(gap.kp_id == _TARGET for gap in gaps)
