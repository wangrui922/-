"""T04 · T3 掌握度算法层单测（纯函数，不碰 DB / 不碰网络）。

覆盖四块：
    1. ``decay_weight``          时效衰减半衰期是否准确；
    2. ``grade_level``           分级边界 + 「样本不足降一级」；
    3. ``classify_error``        五类错因的关键词命中与**优先级仲裁**；
    4. ``rank_recommendations``  推荐排序、level 3 过滤与学霸兜底。

这些用例是 T04 的「地基测试」——集成测试挂了先看这里，
能快速区分是算法错了还是数据链路错了。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.constants import MASTERY_HALF_LIFE_DAYS
from app.core.error_categories import (
    ERROR_CALC,
    ERROR_CATEGORIES,
    ERROR_CHECK,
    ERROR_KNOWLEDGE,
    ERROR_METHOD,
    ERROR_READ,
    ERROR_UNCLASSIFIED,
)
from app.services.mastery_engine import (
    AttemptEvent,
    ErrorEvidence,
    MasteryEntry,
    MasteryResult,
    aggregate_error_patterns,
    as_utc,
    classify_batch,
    classify_error,
    compute_mastery,
    compute_priority,
    decay_weight,
    grade_level,
    rank_recommendations,
    top_error_category,
)

NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _event(is_correct: bool, days_ago: float = 0.0, weight: float = 1.0) -> AttemptEvent:
    """造一条练习事件。"""
    return AttemptEvent(
        kp_id="kp-1",
        is_correct=is_correct,
        occurred_at=NOW - timedelta(days=days_ago),
        weight=weight,
    )


# ==================================================================== 时效衰减
def test_decay_weight_half_life():
    """半衰期语义：0 天 = 1.0，14 天 = 0.5，28 天 = 0.25，且单调递减。"""
    assert decay_weight(0) == 1.0
    assert round(decay_weight(MASTERY_HALF_LIFE_DAYS), 6) == 0.5
    assert round(decay_weight(MASTERY_HALF_LIFE_DAYS * 2), 6) == 0.25

    series = [decay_weight(day) for day in range(0, 60, 5)]
    assert all(later < earlier for earlier, later in zip(series, series[1:])), series
    assert all(0.0 < value <= 1.0 for value in series), series


def test_decay_weight_handles_negative_and_zero_half_life():
    """负天数按 0 处理（未来时间不加成）；非法半衰期回退默认值，不崩。"""
    assert decay_weight(-10) == 1.0
    assert decay_weight(14, half_life_days=0) == decay_weight(14, half_life_days=MASTERY_HALF_LIFE_DAYS)


def test_as_utc_normalizes_naive_datetime():
    """SQLite 读回的 naive datetime 会被补成 UTC aware，避免相减 TypeError。"""
    naive = datetime(2025, 6, 1, 12, 0, 0)
    assert as_utc(naive) == NOW
    assert as_utc(None) is None


# ==================================================================== 掌握度
def test_compute_mastery_empty_events():
    """没有任何事件 -> 全零结果，level 0，不报错。"""
    result = compute_mastery([], now=NOW)
    assert result == MasteryResult()
    assert result.level == 0
    assert result.last_practiced_at is None


def test_compute_mastery_basic_counters():
    """基础计数：attempts / correct_count / error_rate / last_practiced_at。"""
    events = [_event(True), _event(True), _event(False, days_ago=3)]
    result = compute_mastery(events, now=NOW)

    assert result.attempts == 3
    assert result.correct_count == 2
    assert round(result.error_rate, 4) == round(1 / 3, 4)
    assert result.last_practiced_at == NOW
    assert 0.0 <= result.score <= 1.0


def test_compute_mastery_pulled_toward_prior_when_few_samples():
    """样本少时被先验拽回 0.5：只做对 1 题不应判高分。"""
    single = compute_mastery([_event(True)], now=NOW)
    many = compute_mastery([_event(True) for _ in range(20)], now=NOW)

    assert single.score < many.score, (single, many)
    assert single.score < 0.8, single  # 1 次全对也不能接近满分
    assert many.score > 0.85, many


def test_compute_mastery_recent_data_weighs_more():
    """同样的对错分布，越近的数据有效样本量越大、结论越极端。"""
    fresh = compute_mastery([_event(False) for _ in range(5)], now=NOW)
    stale = compute_mastery([_event(False, days_ago=60) for _ in range(5)], now=NOW)

    assert fresh.effective_attempts > stale.effective_attempts
    # 全错时：有效样本越大越往 0 拉，越久远越贴近先验 0.5
    assert fresh.score < stale.score, (fresh, stale)
    assert stale.score < 0.5


def test_compute_mastery_respects_event_weight():
    """事件权重直接放大/缩小有效样本量。"""
    full = compute_mastery([_event(True, weight=1.0)], now=NOW)
    half = compute_mastery([_event(True, weight=0.5)], now=NOW)
    assert full.effective_attempts > half.effective_attempts


# ==================================================================== 分级边界
def test_grade_level_boundaries():
    """分级阈值边界：0.40 / 0.60 / 0.85 三条线（样本量给足）。"""
    assert grade_level(0.0, 10.0, 10) == 0
    assert grade_level(0.3999, 10.0, 10) == 0
    assert grade_level(0.40, 10.0, 10) == 1
    assert grade_level(0.5999, 10.0, 10) == 1
    assert grade_level(0.60, 10.0, 10) == 2
    assert grade_level(0.8499, 10.0, 10) == 2
    assert grade_level(0.85, 10.0, 10) == 3
    assert grade_level(1.0, 10.0, 10) == 3


def test_grade_level_downgrades_when_samples_insufficient():
    """样本不足逐级降：分够但 eff 不够，拿不到高等级。"""
    assert grade_level(0.90, 10.0, 10) == 3  # eff >= 5 -> 3
    assert grade_level(0.90, 3.2, 4) == 2    # 5 > eff >= 3 -> 降到 2
    assert grade_level(0.90, 1.0, 1) == 1    # eff < 3 -> 再降到 1
    assert grade_level(0.70, 2.9, 3) == 1    # level2 也要求 eff >= 3


def test_grade_level_zero_attempts_always_level_zero():
    """一次都没练过，分数再高也只能是 level 0。"""
    assert grade_level(0.99, 0.0, 0) == 0


# ==================================================================== 错因分类
def test_classify_error_each_category():
    """五类关键词都能各自命中。"""
    assert classify_error("概念不清，定义没记住") == ERROR_KNOWLEDGE
    assert classify_error("解法选错了，思路跑偏") == ERROR_METHOD
    assert classify_error("过程跳步，单位没写") == ERROR_CHECK
    assert classify_error("审题时看错了数字") == ERROR_READ
    assert classify_error("计算失误，进位忘了") == ERROR_CALC
    assert classify_error("说不清楚为什么") == ERROR_UNCLASSIFIED


def test_classify_error_empty_and_none():
    """空文本 / None -> UNCLASSIFIED，不抛异常。"""
    assert classify_error("") == ERROR_UNCLASSIFIED
    assert classify_error("   ") == ERROR_UNCLASSIFIED
    assert classify_error(None) == ERROR_UNCLASSIFIED


def test_classify_error_priority_arbitration():
    """命中多类时按 KNOWLEDGE > METHOD > READ > CHECK > CALC 仲裁。"""
    # 概念 + 计算 -> 取概念（更根因）
    assert classify_error("概念不清，而且还算错了") == ERROR_KNOWLEDGE
    # 方法 + 计算 -> 取方法
    assert classify_error("思路错了导致计算也错") == ERROR_METHOD
    # 步骤 + 计算 -> 取步骤
    assert classify_error("漏写步骤，顺便算错") == ERROR_CHECK
    # 审题 + 计算 -> 取审题
    assert classify_error("看错题目，算错答案") == ERROR_READ


def test_classify_error_is_case_insensitive():
    """英文关键词大小写不敏感。"""
    assert classify_error("CONCEPT gap") == ERROR_KNOWLEDGE
    assert classify_error("Calc mistake") == ERROR_CALC


def test_classify_batch_keeps_order():
    """批量分类返回等长、同序的结果。"""
    texts = ["计算失误", "概念不清", "", None]
    assert classify_batch(texts) == [ERROR_CALC, ERROR_KNOWLEDGE, ERROR_UNCLASSIFIED, ERROR_UNCLASSIFIED]


# ==================================================================== 错因聚合
def test_aggregate_error_patterns_always_five_categories():
    """恒返回 5 类（count 可为 0），保证前端环形图不缺段。"""
    patterns = aggregate_error_patterns([ErrorEvidence(text="计算失误")])

    assert len(patterns) == 5
    assert {item.category for item in patterns} == set(ERROR_CATEGORIES)
    assert patterns[0].category == ERROR_CALC  # count 最大的排最前
    assert patterns[0].count == 1
    assert round(patterns[0].ratio, 4) == 1.0
    assert sum(item.count for item in patterns) == 1


def test_aggregate_error_patterns_empty_input():
    """空输入也返回 5 类，全部 count=0 / ratio=0，不出现除零。"""
    patterns = aggregate_error_patterns([])

    assert len(patterns) == 5
    assert all(item.count == 0 for item in patterns)
    assert all(item.ratio == 0.0 for item in patterns)
    assert all(item.sample_hits == [] for item in patterns)


def test_aggregate_error_patterns_ratio_and_samples():
    """占比之和约等于 1；样例文本去重且不超过上限。"""
    evidences = [ErrorEvidence(text="计算失误")] * 3 + [ErrorEvidence(text="概念不清")]
    patterns = aggregate_error_patterns(evidences)

    calc = next(item for item in patterns if item.category == ERROR_CALC)
    concept = next(item for item in patterns if item.category == ERROR_KNOWLEDGE)

    assert calc.count == 3
    assert concept.count == 1
    assert round(calc.ratio + concept.ratio, 4) == 1.0
    assert calc.sample_hits == ["计算失误"]  # 重复文本只留一条


def test_top_error_category():
    """取最高频类别；并列时按优先级仲裁；全零返回 None。"""
    assert top_error_category({ERROR_CALC: 3, ERROR_KNOWLEDGE: 1}) == ERROR_CALC
    # 并列 -> KNOWLEDGE 优先级高于 CALC
    assert top_error_category({ERROR_CALC: 2, ERROR_KNOWLEDGE: 2}) == ERROR_KNOWLEDGE
    assert top_error_category({}) is None
    assert top_error_category({ERROR_CALC: 0}) is None


# ==================================================================== 推荐排序
def _entry(kp_id: str, name: str, score: float, level: int, error_rate: float, eff: float = 5.0) -> MasteryEntry:
    """造一条推荐输入项。"""
    return MasteryEntry(
        kp_id=kp_id,
        kp_name=name,
        result=MasteryResult(
            score=score,
            level=level,
            attempts=10,
            correct_count=int(10 * (1 - error_rate)),
            error_rate=error_rate,
            effective_attempts=eff,
            last_practiced_at=NOW,
        ),
        error_counts={ERROR_CALC: 2},
    )


def test_compute_priority_monotonic():
    """越不会、错得越多，优先级越高，且恒在 [0,1]。"""
    weak = compute_priority(MasteryResult(score=0.1, error_rate=0.9, effective_attempts=5))
    strong = compute_priority(MasteryResult(score=0.9, error_rate=0.1, effective_attempts=5))

    assert weak > strong
    assert 0.0 <= strong <= weak <= 1.0


def test_rank_recommendations_orders_by_priority():
    """按优先级降序返回，最薄弱的排第一。"""
    entries = [
        _entry("kp-a", "分数加减", 0.80, 2, 0.20),
        _entry("kp-b", "一元一次方程", 0.20, 0, 0.80),
        _entry("kp-c", "行程问题", 0.50, 1, 0.50),
    ]
    result = rank_recommendations(entries, [], 5, {})

    assert [item.kp_id for item in result] == ["kp-b", "kp-c", "kp-a"]
    assert result[0].priority >= result[1].priority >= result[2].priority
    assert result[0].top_error_category == ERROR_CALC
    assert "一元一次方程" in result[0].reason


def test_rank_recommendations_filters_mastered():
    """level 3（熟练）默认不推。"""
    entries = [
        _entry("kp-a", "已熟练", 0.95, 3, 0.05),
        _entry("kp-b", "还薄弱", 0.30, 0, 0.70),
    ]
    result = rank_recommendations(entries, [], 5, {})

    assert [item.kp_id for item in result] == ["kp-b"]


def test_rank_recommendations_fallback_when_all_mastered():
    """全部熟练（学霸场景）时放开过滤，仍给 Top N，不返回空。"""
    entries = [
        _entry("kp-a", "熟练甲", 0.95, 3, 0.05),
        _entry("kp-b", "熟练乙", 0.88, 3, 0.12),
    ]
    result = rank_recommendations(entries, [], 5, {})

    assert len(result) == 2
    assert result[0].kp_id == "kp-b"  # 错误率更高的排前面


def test_rank_recommendations_respects_limit_and_questions():
    """limit 生效；``suggested_question_ids`` 从入参映射填充。"""
    entries = [_entry(f"kp-{i}", f"知识点{i}", 0.2 + i * 0.1, 0, 0.5) for i in range(6)]
    questions = {"kp-0": ["q1", "q2", "q3", "q4", "q5"]}
    result = rank_recommendations(entries, [], 3, questions)

    assert len(result) == 3
    first = next((item for item in result if item.kp_id == "kp-0"), None)
    assert first is not None
    assert first.suggested_question_ids == ["q1", "q2", "q3"]  # 最多 3 道


def test_rank_recommendations_empty_input():
    """无掌握度数据 -> 空列表（前端画空态，不报错）。"""
    assert rank_recommendations([], [], 5, {}) == []


def test_rank_recommendations_uses_global_pattern_fallback():
    """该知识点没有自己的错因记录时，回退用全局最高频错因。"""
    entry = MasteryEntry(
        kp_id="kp-x",
        kp_name="无错因记录点",
        result=MasteryResult(score=0.3, level=0, attempts=4, correct_count=1, error_rate=0.75),
        error_counts={},
    )
    patterns = aggregate_error_patterns([ErrorEvidence(text="概念不清")])
    result = rank_recommendations([entry], patterns, 5, {})

    assert result[0].top_error_category == ERROR_KNOWLEDGE
