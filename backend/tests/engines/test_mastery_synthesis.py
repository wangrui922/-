"""C02 Phase 3 · L3 综合引擎（纯函数）单测。

护栏重点：
    1. **golden 真值**（铁律 6 · N1 铁律）：错因计数/占比、薄弱 KP 优先级、
       趋势斜率全部手算写死，防止公共分支被改坏而对称互比看不出来；
    2. **机器码陷阱**：错因关键词表并不覆盖 ``KNOWLEDGE`` / ``READ`` 机器码，
       直接丢进分类器会被判成 UNCLASSIFIED 丢弃 → 必须走归一化；
    3. **溯源边**：三类综合都必须带 ``ref_kp_ids`` / ``ref_event_ids``，
       没有证据时必须为空（服务层据此拒写）；
    4. **确定性**：``now`` 注入后结果可复现（不许读墙上时钟）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.engines.mastery_synthesis import (
    SYNTHESIS_ERROR_PATTERN,
    SYNTHESIS_TREND,
    SYNTHESIS_WEAK_CHAIN,
    SynthesisEvent,
    synthesize_error_patterns,
    synthesize_student,
    synthesize_trend,
    synthesize_weak_chains,
)
from app.services.mastery_engine import MasteryResult, PrerequisiteEdge

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ev(
    idx: int,
    *,
    event_type: str,
    kp_id: Optional[str] = None,
    at: Optional[datetime] = None,
    error_type: Optional[str] = None,
    error_text: Optional[str] = None,
) -> SynthesisEvent:
    """造一条综合事件（``is_correct`` 语义与事件类型一致）。"""
    if event_type == "CORRECT":
        is_correct: Optional[bool] = True
    elif event_type == "ERROR":
        is_correct = False
    else:
        is_correct = None
    return SynthesisEvent(
        event_id=f"ev-{idx}",
        event_type=event_type,
        kp_id=kp_id,
        occurred_at=at or NOW,
        is_correct=is_correct,
        error_text=error_text,
        error_type=error_type,
    )


# ================================================================ 1. ERROR_PATTERN golden
def test_error_pattern_golden_counts_and_ratio() -> None:
    """3 条错误：2 条知识点缺 + 1 条计算错 → 计数/占比/主导错因全部手算。"""
    events = [
        _ev(1, event_type="ERROR", kp_id="kp-weak", error_type="KNOWLEDGE"),
        _ev(2, event_type="ERROR", kp_id="kp-weak", error_type="KNOWLEDGE"),
        _ev(3, event_type="ERROR", kp_id="kp-mid", error_type="CALC"),
    ]

    res = synthesize_error_patterns(events, now=NOW)

    assert res.synthesis_type == SYNTHESIS_ERROR_PATTERN
    payload = res.payload
    assert payload["total_errors"] == 3
    assert payload["total_events"] == 3
    # 主导错因 = 计数最高者（KNOWLEDGE 2 条 > CALC 1 条）
    assert payload["dominant_category"] == "KNOWLEDGE"
    by_cat = {p["category"]: p for p in payload["patterns"]}
    assert by_cat["KNOWLEDGE"]["count"] == 2
    assert by_cat["KNOWLEDGE"]["ratio"] == 0.6667  # 2/3，引擎保留 4 位
    assert by_cat["CALC"]["count"] == 1
    assert by_cat["CALC"]["ratio"] == 0.3333  # 1/3
    assert by_cat["METHOD"]["count"] == 0
    # 置信度随错误样本量增长：1 - 0.5 ** min(3, 10) = 0.875
    assert res.confidence == 0.875
    # 溯源边：错误事件 id + 出错知识点
    assert res.ref_event_ids == ["ev-1", "ev-2", "ev-3"]
    assert res.ref_kp_ids == ["kp-mid", "kp-weak"]


def test_error_pattern_machine_code_not_silently_dropped() -> None:
    """机器码 KNOWLEDGE / READ 必须能被识别（关键词表认不出裸机器码）。

    这是真实踩过的坑：``classify_error("KNOWLEDGE")`` 命中不到任何关键词 →
    判成 UNCLASSIFIED → 聚合计数为 0 → 主导错因丢失。
    """
    events = [
        _ev(1, event_type="ERROR", kp_id="kp-a", error_type="KNOWLEDGE"),
        _ev(2, event_type="ERROR", kp_id="kp-a", error_type="READ"),
    ]

    res = synthesize_error_patterns(events, now=NOW)

    by_cat = {p["category"]: p for p in res.payload["patterns"]}
    assert by_cat["KNOWLEDGE"]["count"] == 1
    assert by_cat["READ"]["count"] == 1
    assert res.payload["dominant_category"] in ("KNOWLEDGE", "READ")


def test_error_pattern_prefers_human_error_text() -> None:
    """有人类错因文本时以文本为准（文本经关键词表分类）。"""
    events = [
        _ev(1, event_type="ERROR", kp_id="kp-a", error_text="审题时漏看了条件"),
    ]

    res = synthesize_error_patterns(events, now=NOW)

    by_cat = {p["category"]: p for p in res.payload["patterns"]}
    assert by_cat["READ"]["count"] == 1
    assert by_cat["READ"]["sample_hits"] == ["审题时漏看了条件"]


def test_error_pattern_top_weak_kps_golden_priority() -> None:
    """Top3 薄弱 KP 的分值/等级/优先级为手算真值（K=3, prior=0.5, decay=1）。"""
    events = [
        # kp-weak：3 次全错 → accuracy 0, eff 3 → score (0.5*3+0)/(3+3)=0.25
        _ev(1, event_type="ERROR", kp_id="kp-weak"),
        _ev(2, event_type="ERROR", kp_id="kp-weak"),
        _ev(3, event_type="ERROR", kp_id="kp-weak"),
        # kp-strong：3 次全对 → score (0.5*3+1*3)/(3+3)=0.75
        _ev(4, event_type="CORRECT", kp_id="kp-strong"),
        _ev(5, event_type="CORRECT", kp_id="kp-strong"),
        _ev(6, event_type="CORRECT", kp_id="kp-strong"),
    ]

    res = synthesize_error_patterns(events, now=NOW)

    weak = res.payload["top_weak_kps"]
    assert len(weak) == 2
    # priority(weak) = 0.6*(1-0.25) + 0.3*1.0 + 0.1*min(3,5)/5 = 0.45+0.3+0.06
    assert weak[0]["kp_id"] == "kp-weak"
    assert weak[0]["score"] == 0.25
    assert weak[0]["level"] == 0
    assert weak[0]["priority"] == 0.81
    assert weak[0]["error_count"] == 3
    # priority(strong) = 0.6*(1-0.75) + 0 + 0.06 = 0.21
    assert weak[1]["kp_id"] == "kp-strong"
    assert weak[1]["score"] == 0.75
    assert weak[1]["level"] == 2
    assert weak[1]["priority"] == 0.21


def test_error_pattern_empty_events_no_refs() -> None:
    """无事件 → 溯源边必须为空（服务层据此拒写，不编造 L3）。"""
    res = synthesize_error_patterns([], now=NOW)
    assert res.ref_kp_ids == []
    assert res.ref_event_ids == []
    assert res.confidence == 0.0


# ================================================================ 2. WEAK_CHAIN golden
def test_weak_chain_detects_gap_and_refs() -> None:
    """前置缺口：后置已练但前置没练 → 缺口成立，溯源边含后置 + 前置。"""
    mastery = {
        "kp-target": MasteryResult(
            score=0.8, level=2, attempts=5, correct_count=4, effective_attempts=5.0
        ),
        # kp-pre 缺失（完全没练过）→ missing=True
    }
    edges = [PrerequisiteEdge(pre_kp_id="kp-pre", kp_id="kp-target", weight=1.0)]

    res = synthesize_weak_chains(mastery, edges, now=NOW)

    assert res.synthesis_type == SYNTHESIS_WEAK_CHAIN
    assert res.payload["gap_count"] == 1
    gap = res.payload["gaps"][0]
    assert gap["kp_id"] == "kp-target"
    assert gap["pre_kp_id"] == "kp-pre"
    assert gap["missing"] is True
    assert gap["pre_score"] == 0.0
    # 溯源边必须同时含后置与前置知识点
    assert res.ref_kp_ids == ["kp-pre", "kp-target"]
    # 链路来自掌握度 + 图谱推导，没有单事件溯源
    assert res.ref_event_ids == []


def test_weak_chain_no_gap_when_preficient() -> None:
    """前置已达标（score >= 0.5）→ 无缺口 → 溯源边为空（不写库）。"""
    mastery = {
        "kp-target": MasteryResult(score=0.8, attempts=5, effective_attempts=5.0),
        "kp-pre": MasteryResult(score=0.9, attempts=5, effective_attempts=5.0),
    }
    edges = [PrerequisiteEdge(pre_kp_id="kp-pre", kp_id="kp-target", weight=1.0)]

    res = synthesize_weak_chains(mastery, edges, now=NOW)

    assert res.payload["gap_count"] == 0
    assert res.ref_kp_ids == []


def test_weak_chain_ignores_unpracticed_target() -> None:
    """后置完全没练过 → 不下缺口结论（沿用 detect_prerequisite_gaps 口径）。"""
    mastery = {
        "kp-target": MasteryResult(score=0.0, attempts=0, effective_attempts=0.0),
    }
    edges = [PrerequisiteEdge(pre_kp_id="kp-pre", kp_id="kp-target", weight=1.0)]

    res = synthesize_weak_chains(mastery, edges, now=NOW)

    assert res.payload["gap_count"] == 0
    assert res.ref_kp_ids == []


# ================================================================ 3. TREND golden
def test_trend_slope_golden() -> None:
    """趋势斜率手算真值。

    窗口 4 周，事件落在：第 0 周（25 天前）全错 → 0.0；
    第 1 周（18 天前）1 对 1 错 → 0.5；第 2 周（11 天前）全对 → 1.0；
    第 3 周（4 天前）无事件 → 不进序列。
    斜率 = 最小二乘，x=0,1,2、y=0.0,0.5,1.0 完全线性 → 每周 +0.5。
    """
    events = [
        _ev(1, event_type="ERROR", kp_id="kp-a", at=NOW - timedelta(days=25)),
        _ev(2, event_type="ERROR", kp_id="kp-a", at=NOW - timedelta(days=25)),
        _ev(3, event_type="CORRECT", kp_id="kp-a", at=NOW - timedelta(days=18)),
        _ev(4, event_type="ERROR", kp_id="kp-a", at=NOW - timedelta(days=18)),
        _ev(5, event_type="CORRECT", kp_id="kp-a", at=NOW - timedelta(days=11)),
        _ev(6, event_type="CORRECT", kp_id="kp-a", at=NOW - timedelta(days=11)),
    ]

    res = synthesize_trend(events, now=NOW)

    assert res.synthesis_type == SYNTHESIS_TREND
    assert res.payload["slope_per_week"] == 0.5
    # 窗口内判定事件 6 条（2+2+2），正确 3 条 → 0.5
    assert res.payload["overall_accuracy"] == 0.5
    assert res.payload["judged_events"] == 6
    # 最新一周无事件 → 回退用总体正确率
    assert res.payload["recent_accuracy"] == 0.5
    weekly = res.payload["weekly"]
    assert [w["count"] for w in weekly] == [2, 2, 2, 0]
    assert [w["accuracy"] for w in weekly] == [0.0, 0.5, 1.0, None]
    # 溯源边：全部事件 + 全部知识点
    assert len(res.ref_event_ids) == 6
    assert res.ref_kp_ids == ["kp-a"]
    # 置信度 = 1 - 0.5 ** min(6, 10) = 0.9844
    assert res.confidence == 0.9844


def test_trend_ignores_unjudged_events_in_accuracy() -> None:
    """HINT / REVIEW 等无判定事件不进正确率，但计入总活跃量。"""
    events = [
        _ev(1, event_type="CORRECT", kp_id="kp-a"),
        _ev(2, event_type="HINT", kp_id="kp-a"),
        _ev(3, event_type="REVIEW", kp_id="kp-a"),
    ]

    res = synthesize_trend(events, now=NOW)

    assert res.payload["judged_events"] == 1
    assert res.payload["total_events"] == 3
    assert res.payload["overall_accuracy"] == 1.0


def test_trend_excludes_events_outside_window() -> None:
    """超出 4 周窗口的旧事件不计入周窗正确率。

    同时锁死「分子分母同口径」：**窗口外的老错不得拉低窗口内正确率**。
    （早期版本分子只累加窗口内、分母却用全部判定事件 → 这里会得到 0.5。）
    """
    events = [
        _ev(1, event_type="ERROR", kp_id="kp-a", at=NOW - timedelta(days=60)),
        _ev(2, event_type="CORRECT", kp_id="kp-a", at=NOW),
    ]

    res = synthesize_trend(events, now=NOW)

    weekly = res.payload["weekly"]
    assert sum(w["count"] for w in weekly) == 1  # 只有最近那条进窗口
    assert res.payload["overall_accuracy"] == 1.0
    assert res.payload["judged_events"] == 1
    # 总活跃量仍含窗口外事件（活动量与正确率分开统计）
    assert res.payload["total_events"] == 2


def test_trend_empty_events_no_refs() -> None:
    """无事件 → 溯源边为空（拒写）。"""
    res = synthesize_trend([], now=NOW)
    assert res.ref_kp_ids == []
    assert res.ref_event_ids == []
    assert res.confidence == 0.0


# ================================================================ 4. 编排入口
def test_synthesize_student_returns_three_types() -> None:
    """编排入口一次产出三类综合，键名与枚举一致。"""
    events = [
        _ev(1, event_type="ERROR", kp_id="kp-a", error_type="CALC"),
        _ev(2, event_type="CORRECT", kp_id="kp-a"),
    ]
    mastery = {
        "kp-a": MasteryResult(score=0.5, attempts=2, effective_attempts=2.0),
    }
    edges = [PrerequisiteEdge(pre_kp_id="kp-pre", kp_id="kp-a", weight=1.0)]

    results = synthesize_student(events, mastery, edges, now=NOW)

    assert set(results.keys()) == {
        SYNTHESIS_ERROR_PATTERN,
        SYNTHESIS_WEAK_CHAIN,
        SYNTHESIS_TREND,
    }
    assert results[SYNTHESIS_ERROR_PATTERN].ref_event_ids == ["ev-1"]
    assert results[SYNTHESIS_TREND].ref_event_ids == ["ev-1", "ev-2"]
    assert results[SYNTHESIS_WEAK_CHAIN].ref_kp_ids == ["kp-a", "kp-pre"]


def test_synthesize_student_is_deterministic() -> None:
    """同一输入 + 同一 now → 结果完全一致（不许读墙上时钟）。"""
    events = [
        _ev(1, event_type="ERROR", kp_id="kp-a", error_type="CALC"),
        _ev(2, event_type="CORRECT", kp_id="kp-a"),
    ]
    mastery = {"kp-a": MasteryResult(score=0.5, attempts=2, effective_attempts=2.0)}

    first = synthesize_student(events, mastery, [], now=NOW)
    second = synthesize_student(events, mastery, [], now=NOW)

    for key in first:
        assert first[key] == second[key]
