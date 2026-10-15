# -*- coding: utf-8 -*-
"""引擎层 · 难度引擎单测（V6.5 Sprint 6 · T05）。

覆盖目标：
    1. 边界钳制（最低 1 / 最高 5 难度）；
    2. 冷启动（无历史数据 / 样本不足）；
    3. 掌握度极高 / 极低时的取向；
    4. ``adaptive.difficulty.*`` 配置确实生效（带宽、惯性、各项系数）；
    5. 异常入参（None / 越界 / 脏字符串）走兜底而不是抛异常；
    6. 幂等性：同输入两次调用结果完全一致；
    7. DNA 适配（``difficulty_input_from_dna``）与 ``system_config`` 读取。

运行方式：
    * 有 pytest：``python -m pytest tests/engines/test_difficulty.py``；
    * 无 pytest（本机离线环境）：``python tests/engines/test_difficulty.py``
      ——文件末尾自带 runner，逐条执行本模块所有 ``test_*`` 函数。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 让 standalone 模式（无 pytest 时直接 python 跑）也能 import app
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.engines.config import (  # noqa: E402
    CONFIG_KEYS,
    DEFAULT_ADAPTIVE_CONFIG,
    AdaptiveConfig,
    build_adaptive_config,
    read_adaptive_config,
)
from app.engines.difficulty import (  # noqa: E402
    REASON_COLD_START,
    REASON_DISABLED,
    REASON_PULL_DOWN,
    REASON_PUSH_UP,
    REASON_STEADY,
    decide_difficulty,
    decide_difficulty_payload,
    difficulty_input_from_dna,
)
from app.engines.types import (  # noqa: E402
    DEFAULT_DIFFICULTY,
    DIFFICULTY_MAX,
    DIFFICULTY_MIN,
    DifficultyBand,
    DifficultyInput,
    nearest_level,
    snap_levels,
    to_bool,
    to_float,
    to_int,
)


# ============================================================ 测试工具
def _input(
    mastery: float = 0.5,
    confidence: float = 1.0,
    practice_count: int = 10,
    error_rate: float = 0.0,
    hint_dependency: str = "medium",
    current_difficulty: float = 3.0,
    recent_accuracy: Optional[float] = None,
) -> DifficultyInput:
    """造一个「样本充足」的标准入参（默认中等掌握度）。"""
    return DifficultyInput(
        mastery=mastery,
        confidence=confidence,
        practice_count=practice_count,
        error_rate=error_rate,
        hint_dependency=hint_dependency,
        current_difficulty=current_difficulty,
        recent_accuracy=recent_accuracy,
    )


def _strong() -> DifficultyInput:
    """掌握度接近满分的学霸入参（当前难度 3）。"""
    return _input(mastery=0.9, confidence=1.0, practice_count=20)


def _dna() -> Dict[str, Any]:
    """造一份 ``get_learning_dna()`` 形态的返回值（两个知识点 + 4 条事件）。"""
    return {
        "student_id": "11111111-1111-1111-1111-111111111111",
        "profile": {
            "student_id": "11111111-1111-1111-1111-111111111111",
            "learning_goal": "improve_grades",
            "hint_dependency": "high",
        },
        "mastery_summary": [
            {
                "knowledge_point_id": "kp-strong",
                "kp_name": "一元一次方程",
                "mastery": 0.86,
                "confidence": 0.94,
                "level": 3,
                "error_rate": 0.08,
                "practice_count": 24,
                "correct_count": 22,
                "wrong_count": 2,
            },
            {
                "knowledge_point_id": "kp-weak",
                "kp_name": "分数加减法",
                "mastery": 0.24,
                "confidence": 0.88,
                "level": 0,
                "error_rate": 0.55,
                "practice_count": 17,
                "correct_count": 8,
                "wrong_count": 9,
            },
        ],
        "recent_events": [
            {"event_type": "CORRECT"},
            {"event_type": "CORRECT"},
            {"event_type": "CORRECT"},
            {"event_type": "ERROR"},
        ],
    }


def _sqlalchemy_available() -> bool:
    """本机是否装了 sqlalchemy（纯单测不强依赖，缺失时跳过 DB 相关断言）。"""
    try:  # pragma: no cover - 取决于环境
        import sqlalchemy  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


# ============================================================ 基础类型兜底
def test_to_float_and_int_fallback():
    """脏值转换：字符串可转即转，不可转/NaN/inf 一律回退缺省值。"""
    assert to_float("0.25") == 0.25
    assert to_float(None, 0.7) == 0.7
    assert to_float("abc", 0.7) == 0.7
    assert to_float(float("nan"), 0.7) == 0.7
    assert to_float(True) == 1.0
    assert to_int("7", 3) == 7
    assert to_int("abc", 3) == 3


def test_to_bool_variants():
    """布尔解析覆盖常见写法，识别不了时回退缺省值。"""
    assert to_bool("true") is True
    assert to_bool("YES") is True
    assert to_bool("off") is False
    assert to_bool("0") is False
    assert to_bool(True) is True
    assert to_bool("乱写", True) is True


def test_difficulty_input_normalizes_dirty_values():
    """异常入参（None/越界/脏字符串）在构造期被归一化，不抛异常。"""
    payload = DifficultyInput(
        mastery="0.6",
        confidence=9,
        error_rate=-5,
        practice_count=-7,
        hint_dependency="weird",
        current_difficulty="3.7",
        recent_accuracy="abc",
    )
    assert payload.mastery == 0.6
    assert payload.confidence == 1.0
    assert payload.error_rate == 0.0
    assert payload.practice_count == 0
    assert payload.hint_dependency == "medium"
    assert payload.current_difficulty == 3.7
    assert payload.recent_accuracy == 0.0


def test_difficulty_input_mastery_clamped():
    """掌握度/置信度/错误率均被夹紧到 [0,1]。"""
    payload = DifficultyInput(mastery=-3, confidence=42.0, error_rate=99)
    assert payload.mastery == 0.0
    assert payload.confidence == 1.0
    assert payload.error_rate == 1.0


def test_difficulty_input_current_difficulty_fallback():
    """当前难度：不可解析回退默认 3，越界数值夹紧到坐标边界。"""
    assert DifficultyInput(current_difficulty=None).current_difficulty == DEFAULT_DIFFICULTY
    assert DifficultyInput(current_difficulty="x").current_difficulty == DEFAULT_DIFFICULTY
    assert DifficultyInput(current_difficulty=0).current_difficulty == DIFFICULTY_MIN
    assert DifficultyInput(current_difficulty=99).current_difficulty == DIFFICULTY_MAX


def test_difficulty_input_none_mastery_never_raises():
    """``mastery=None`` 这类脏输入也能构造出合法入参（冷启动口径）。"""
    payload = DifficultyInput(mastery=None, confidence=None, recent_accuracy=None)
    assert payload.mastery == 0.0
    assert payload.confidence == 0.0
    assert payload.recent_accuracy is None
    assert decide_difficulty(payload).cold_start is True


# ============================================================ 边界钳制
def test_band_never_exceeds_low_bound():
    """最弱学生：难度带被钳在下界 1，且不少于一档。"""
    payload = _input(mastery=0.0, error_rate=1.0, practice_count=20, hint_dependency="high")
    band = decide_difficulty(payload)
    assert band.lo == DIFFICULTY_MIN
    assert band.levels == (1,)
    assert band.target == DIFFICULTY_MIN
    assert band.reason == REASON_PULL_DOWN


def test_band_never_exceeds_high_bound():
    """最强学生：难度带被钳在上界 5，``hi`` 不得超过 5。"""
    payload = _input(
        mastery=1.0,
        error_rate=0.0,
        practice_count=20,
        hint_dependency="low",
        recent_accuracy=1.0,
        current_difficulty=5,
    )
    band = decide_difficulty(payload)
    assert band.hi == DIFFICULTY_MAX
    assert band.target == DIFFICULTY_MAX
    assert band.levels == (5,)
    assert band.lo >= DIFFICULTY_MIN


def test_band_ordering_always_lo_le_hi():
    """无论配置多脏，``lo <= hi`` 恒成立。"""
    dirty = build_adaptive_config(
        {"adaptive.difficulty.band_width": "-5", "adaptive.difficulty.max": "0.5"}
    )
    band = decide_difficulty(_input(mastery=0.9), dirty)
    assert band.lo <= band.hi


# ============================================================ 冷启动
def test_cold_start_without_history():
    """无掌握数据时走冷启动：以当前难度为中心，宽度用冷启动探索带。"""
    payload = DifficultyInput(mastery=0.0, practice_count=0, current_difficulty=3)
    band = decide_difficulty(payload)
    assert band.cold_start is True
    assert band.reason == REASON_COLD_START
    assert band.target == 3.0
    assert band.lo == 2.0
    assert band.hi == 4.0
    assert band.levels == (3,)


def test_cold_start_threshold_is_configurable():
    """冷启动样本阈值来自 adaptive_config（默认 3）。"""
    below = decide_difficulty(_input(mastery=0.9, practice_count=2))
    at = decide_difficulty(_input(mastery=0.9, practice_count=3))
    assert below.cold_start is True
    assert at.cold_start is False

    relaxed = build_adaptive_config({"adaptive.difficulty.cold_start_samples": "8"})
    assert decide_difficulty(_input(mastery=0.9, practice_count=5), relaxed).cold_start is True


def test_cold_start_cold_width_config_applies():
    """冷启动带宽由 ``adaptive.difficulty.cold_start_width`` 控制。"""
    wider = build_adaptive_config({"adaptive.difficulty.cold_start_width": "2.0"})
    band = decide_difficulty(DifficultyInput(practice_count=0, current_difficulty=3), wider)
    assert band.lo == 1.0
    assert band.hi == 5.0


def test_cold_start_ignores_mastery():
    """冷启动不信掌握度：两次不同的 mastery 必须给出同一个难度带。"""
    weakish = DifficultyInput(mastery=0.1, practice_count=1, current_difficulty=3)
    strongish = DifficultyInput(mastery=0.95, practice_count=1, current_difficulty=3)
    assert decide_difficulty(weakish) == decide_difficulty(strongish)


def test_cold_start_at_lowest_difficulty():
    """当前难度已在下界时，冷启动带只往上升的一侧展开。"""
    band = decide_difficulty(DifficultyInput(practice_count=0, current_difficulty=1))
    assert band.lo == DIFFICULTY_MIN
    assert band.hi == 2.0
    assert band.levels == (1,)


# ============================================================ 掌握度取向
def test_high_mastery_pushes_up():
    """掌握度高 → 目标难度上推，落带到最高档位。"""
    band = decide_difficulty(_input(mastery=1.0, practice_count=20, current_difficulty=3))
    assert band.target == 4.4
    assert band.levels == (5,)
    assert band.reason == REASON_PUSH_UP
    assert band.cold_start is False


def test_low_mastery_pulls_down():
    """掌握度低 + 错误率高 → 目标难度下拉到最低档位。"""
    band = decide_difficulty(
        _input(mastery=0.0, error_rate=1.0, practice_count=20, current_difficulty=3)
    )
    assert band.target == DIFFICULTY_MIN
    assert band.levels == (1,)
    assert band.reason == REASON_PULL_DOWN


def test_medium_mastery_stays_steady():
    """掌握度与当前难度匹配时维持不动（原因码 steady）。"""
    band = decide_difficulty(
        _input(mastery=0.5, practice_count=20, recent_accuracy=0.75, current_difficulty=3)
    )
    assert band.target == 3.0
    assert band.reason == REASON_STEADY
    assert band.levels == (3,)


def test_single_side_clamp_no_downgrade_when_mastery_enough():
    """掌握度已达顶峰时，错误率/提示依赖等惩罚也不得把难度拉到当前难度以下。"""
    full = decide_difficulty(_input(mastery=1.0, practice_count=20, current_difficulty=5))
    assert full.target == DIFFICULTY_MAX
    assert full.levels == (5,)

    punished = decide_difficulty(
        _input(
            mastery=1.0,
            practice_count=20,
            current_difficulty=5,
            error_rate=1.0,
            hint_dependency="high",
            recent_accuracy=0.0,
        )
    )
    # 各项惩罚被 R3 单侧钳制吃掉：仍锁定最高难度
    assert punished.target == DIFFICULTY_MAX
    assert punished.levels == (5,)


def test_single_side_clamp_no_upgrade_when_mastery_short():
    """掌握度撑不住当前难度时，不得被近期正确率推到当前难度以上。"""
    payload = _input(
        mastery=0.2, practice_count=20, recent_accuracy=1.0, current_difficulty=5
    )
    band = decide_difficulty(payload)
    assert band.target <= 5.0
    assert band.reason == REASON_PULL_DOWN


# ============================================================ 配置生效
def test_band_width_config_applies():
    """``adaptive.difficulty.band_width`` 是每侧半宽：带宽 = 2 × 该值。"""
    base = decide_difficulty(_input(mastery=0.5, recent_accuracy=0.75))
    assert round(base.hi - base.lo, 2) == 1.0

    doubled = build_adaptive_config({"adaptive.difficulty.band_width": "1.0"})
    wider = decide_difficulty(_input(mastery=0.5, recent_accuracy=0.75), doubled)
    assert round(wider.hi - wider.lo, 2) == 2.0


def test_confidence_widens_band():
    """置信度越低探索带越宽；增益系数置 0 后带宽不再随置信度变化。"""
    confident = decide_difficulty(_input(mastery=0.5, confidence=1.0, recent_accuracy=0.75))
    guessing = decide_difficulty(_input(mastery=0.5, confidence=0.0, recent_accuracy=0.75))
    assert (guessing.hi - guessing.lo) > (confident.hi - confident.lo)

    frozen = build_adaptive_config({"adaptive.difficulty.confidence_width_gain": "0"})
    flat_a = decide_difficulty(_input(mastery=0.5, confidence=1.0, recent_accuracy=0.75), frozen)
    flat_b = decide_difficulty(_input(mastery=0.5, confidence=0.0, recent_accuracy=0.75), frozen)
    assert round(flat_a.hi - flat_a.lo, 2) == round(flat_b.hi - flat_b.lo, 2)


def test_inertia_extremes():
    """惯性 0 = 完全听掌握度；惯性 1 = 完全不动。"""
    no_inertia = build_adaptive_config({"adaptive.difficulty.inertia": "0"})
    full_inertia = build_adaptive_config({"adaptive.difficulty.inertia": "1"})
    payload = _input(mastery=0.8, practice_count=20, current_difficulty=3)

    free = decide_difficulty(payload, no_inertia)
    stuck = decide_difficulty(payload, full_inertia)
    assert free.target == 4.2
    assert stuck.target == 3.0
    assert stuck.reason == REASON_STEADY


def test_error_rate_penalty_monotonic():
    """错误率惩罚系数生效：错误率越高，推荐难度越低。"""
    clean = decide_difficulty(_strong())
    half = decide_difficulty(_input(mastery=0.9, error_rate=0.5, practice_count=20))
    dirty = decide_difficulty(_input(mastery=0.9, error_rate=1.0, practice_count=20))
    assert clean.target > half.target > dirty.target


def test_recent_accuracy_gain():
    """近期正确率围绕目标中心双向调节；正好等于中心时不产生位移。"""
    base = decide_difficulty(_strong())
    perfect = decide_difficulty(
        _input(mastery=0.9, practice_count=20, recent_accuracy=1.0)
    )
    poor = decide_difficulty(_input(mastery=0.9, practice_count=20, recent_accuracy=0.5))
    neutral = decide_difficulty(
        _input(mastery=0.9, practice_count=20, recent_accuracy=0.75)
    )
    assert perfect.target > base.target > poor.target
    assert neutral.target == base.target


def test_hint_dependency_offsets():
    """提示依赖：high 降难度、low 升难度、非法值按 medium（中性）处理。"""
    medium = decide_difficulty(_strong())
    needy = decide_difficulty(_input(mastery=0.9, practice_count=20, hint_dependency="high"))
    independent = decide_difficulty(_input(mastery=0.9, practice_count=20, hint_dependency="low"))
    invalid = decide_difficulty(_input(mastery=0.9, practice_count=20, hint_dependency="???"))
    assert needy.target < medium.target < independent.target
    assert invalid.target == medium.target


def test_switch_off_pins_current_difficulty():
    """总开关或难度开关关闭 → 难度带锁定为当前难度，宽度 0。"""
    for key in ("adaptive.enabled", "adaptive.difficulty.enabled"):
        config = build_adaptive_config({key: "false"})
        band = decide_difficulty(_input(mastery=1.0, current_difficulty=5), config)
        assert band.lo == band.hi == band.target == 5.0
        assert band.reason == REASON_DISABLED
        assert band.cold_start is False


def test_reason_shift_threshold_config():
    """``reason_shift`` 决定多小的位移算「推上去/拉下来」。"""
    strict = build_adaptive_config({"adaptive.difficulty.reason_shift": "2.0"})
    loose = build_adaptive_config({"adaptive.difficulty.reason_shift": "0.1"})
    payload = _input(mastery=0.9, practice_count=20, current_difficulty=3)
    assert decide_difficulty(payload, strict).reason == REASON_STEADY
    assert decide_difficulty(payload, loose).reason == REASON_PUSH_UP


# ============================================================ 档位映射
def test_snap_levels_mapping():
    """连续难度带 → 离散档位子集；无交集时就近取一档。"""
    assert snap_levels(1.0, 5.0) == (1, 3, 5)
    assert snap_levels(3.9, 4.9) == (5,)
    assert snap_levels(1.0, 1.5) == (1,)
    assert snap_levels(4.0, 4.0) == (3,)  # 等距时取较小档位
    assert snap_levels(4.9, 4.0) == (5,)


def test_nearest_level_tie_breaks_to_lower():
    """就近取档：距离相同时取较小档位，保证确定性。"""
    assert nearest_level(4.0) == 3
    assert nearest_level(4.2) == 5
    assert nearest_level(1.1) == 1


def test_zero_width_band_still_has_one_level():
    """带宽被配置压成 0 时，仍必须至少返回一档（抽题层不做空值兜底）。"""
    narrow = build_adaptive_config(
        {
            "adaptive.difficulty.band_width": "0",
            "adaptive.difficulty.confidence_width_gain": "0",
        }
    )
    band = decide_difficulty(_strong(), narrow)
    assert band.lo == band.hi == band.target
    assert len(band.levels) == 1


# ============================================================ 出参 / 幂等
def test_band_to_api_is_camel_case():
    """出参 dict 用 camelCase，键集合稳定，API 层可直接返回。"""
    payload = decide_difficulty(_strong()).to_api()
    assert set(payload.keys()) == {
        "lo",
        "hi",
        "target",
        "levels",
        "reason",
        "coldStart",
        "confidence",
    }
    assert isinstance(payload["levels"], list)
    assert isinstance(payload["coldStart"], bool)


def test_payload_helper_matches_to_api():
    """``decide_difficulty_payload`` 与 ``decide_difficulty(...).to_api()`` 一致。"""
    item = _strong()
    assert decide_difficulty_payload(item) == decide_difficulty(item).to_api()


def test_idempotent_same_input_same_output():
    """幂等性：同输入两次调用（含 dict 出口）结果完全一致。"""
    item = _input(mastery=0.72, confidence=0.66, practice_count=9, recent_accuracy=0.8)
    first: DifficultyBand = decide_difficulty(item)
    second: DifficultyBand = decide_difficulty(item)
    assert first == second
    assert first.to_api() == second.to_api()
    # 换一个等价入参对象（同字段值）也必须同结果
    twin = DifficultyInput(
        mastery=0.72,
        confidence=0.66,
        practice_count=9,
        recent_accuracy=0.8,
    )
    assert decide_difficulty(twin) == first


def test_non_difficulty_input_falls_back_to_cold_start():
    """传入非 ``DifficultyInput`` 时不炸，按空入参走冷启动。"""
    band = decide_difficulty(None)  # type: ignore[arg-type]
    assert band.cold_start is True
    assert band.reason == REASON_COLD_START
    assert band.target == DEFAULT_DIFFICULTY


def test_band_dataclass_is_frozen():
    """出参不可变（frozen=True），防止调用方手滑改脏共享对象。"""
    band = decide_difficulty(_strong())
    try:
        band.target = 5.0  # type: ignore[misc]
    except Exception:  # noqa: BLE001 - dataclasses.FrozenInstanceError 等
        return
    raise AssertionError("DifficultyBand 应为不可变数据类")


# ============================================================ DNA 适配
def test_dna_adapter_picks_weakest_point():
    """未指定知识点时自动锁定最薄弱知识点。"""
    payload = difficulty_input_from_dna(_dna(), current_difficulty=3)
    assert payload.knowledge_point_id == "kp-weak"
    assert payload.mastery == 0.24
    assert payload.error_rate == 0.55
    assert payload.practice_count == 17


def test_dna_adapter_picks_specified_point():
    """指定知识点时按 ID 精确取行。"""
    payload = difficulty_input_from_dna(
        _dna(), knowledge_point_id="kp-strong", current_difficulty=3
    )
    assert payload.knowledge_point_id == "kp-strong"
    assert payload.mastery == 0.86


def test_dna_adapter_recent_accuracy_from_events():
    """近期正确率 = CORRECT / (CORRECT + ERROR)；无判对错事件时为 None。"""
    dna = _dna()
    payload = difficulty_input_from_dna(dna)
    assert payload.recent_accuracy == 0.75

    no_judged = dict(dna)
    no_judged["recent_events"] = [{"event_type": "HINT"}, {"event_type": "QUESTIONVIEW"}]
    assert difficulty_input_from_dna(no_judged).recent_accuracy is None


def test_dna_adapter_hint_dependency_and_current():
    """画像里的提示依赖、以及外部传入的当前难度都被带入入参。"""
    payload = difficulty_input_from_dna(_dna(), current_difficulty=5)
    assert payload.hint_dependency == "high"
    assert payload.current_difficulty == 5


def test_dna_adapter_unknown_point_is_cold_start():
    """知识点不存在 / DNA 为空 → 全零入参（走冷启动），不抛异常。"""
    missing = difficulty_input_from_dna(_dna(), knowledge_point_id="kp-nope")
    assert missing.mastery == 0.0
    assert missing.practice_count == 0
    assert missing.knowledge_point_id is None

    for empty in (None, {}, {"mastery_summary": "不是列表"}, {"profile": None}):
        payload = difficulty_input_from_dna(empty)
        assert payload.mastery == 0.0
        assert payload.practice_count == 0
        assert payload.hint_dependency == "medium"
        assert payload.current_difficulty == DEFAULT_DIFFICULTY


def test_end_to_end_dna_to_band():
    """端到端：DNA → 入参 → 难度带 → camelCase dict。"""
    payload = difficulty_input_from_dna(_dna(), knowledge_point_id="kp-strong", current_difficulty=3)
    api = decide_difficulty_payload(payload)
    # kp-strong 掌握度 0.86 但提示依赖 high + 有错误率 → 目标略高于当前难度，
    # 尚未够到最高档（详细推导见 docs/engines-design.md 的样例）
    assert api["target"] == 3.54
    assert api["levels"] == [3]
    assert api["coldStart"] is False
    assert DIFFICULTY_MIN <= api["lo"] <= api["hi"] <= DIFFICULTY_MAX


# ============================================================ adaptive_config
def test_build_adaptive_config_defaults():
    """空配置 = 缺省配置，且全部字段落位。"""
    assert build_adaptive_config({}) == DEFAULT_ADAPTIVE_CONFIG
    assert build_adaptive_config(None) == DEFAULT_ADAPTIVE_CONFIG
    assert DEFAULT_ADAPTIVE_CONFIG.difficulty_band_width == 0.5
    assert DEFAULT_ADAPTIVE_CONFIG.difficulty_cold_start_samples == 3
    assert DEFAULT_ADAPTIVE_CONFIG.difficulty_enabled is True


def test_build_adaptive_config_reads_prefixed_keys():
    """``adaptive.*`` 前缀 key 与短 key 都能解析。"""
    assert build_adaptive_config({"adaptive.difficulty.band_width": "1.5"}).difficulty_band_width == 1.5
    assert build_adaptive_config({"difficulty.band_width": "2.0"}).difficulty_band_width == 2.0
    assert build_adaptive_config({"adaptive.difficulty.inertia": "0.9"}).difficulty_inertia == 0.9


def test_build_adaptive_config_booleans_and_dirty_values():
    """布尔写法多样；脏值回退缺省，未知 key 忽略，None 等同未提供。"""
    assert build_adaptive_config({"adaptive.enabled": "false"}).enabled is False
    assert build_adaptive_config({"adaptive.enabled": "yes"}).enabled is True
    assert build_adaptive_config({"adaptive.difficulty.inertia": "abc"}).difficulty_inertia == 0.3
    assert build_adaptive_config({"adaptive.unknown.key": "1"}) == DEFAULT_ADAPTIVE_CONFIG
    assert (
        build_adaptive_config({"adaptive.difficulty.band_width": None}).difficulty_band_width == 0.5
    )


def test_build_adaptive_config_reversed_bounds_fallback():
    """运营把上下界写反时整体回退内置坐标，避免产出 ``lo > hi`` 的脏组合。"""
    config = build_adaptive_config(
        {"adaptive.difficulty.min": "9", "adaptive.difficulty.max": "2"}
    )
    assert config.difficulty_min == DIFFICULTY_MIN
    assert config.difficulty_max == DIFFICULTY_MAX
    assert config.difficulty_default == DEFAULT_DIFFICULTY


def test_adaptive_config_replace_is_immutable():
    """``replace`` 返回新对象，原对象保持不动。"""
    original = DEFAULT_ADAPTIVE_CONFIG
    tweaked = original.replace(difficulty_band_width=2.0)
    assert tweaked.difficulty_band_width == 2.0
    assert original.difficulty_band_width == 0.5
    assert isinstance(tweaked, AdaptiveConfig)


def test_config_keys_are_prefixed():
    """配置 key 一律以 ``adaptive.`` 打头，且会被全 clamp 一遍。"""
    assert CONFIG_KEYS
    for key in CONFIG_KEYS:
        assert key.startswith("adaptive."), key


class _FakeResult:
    """``db.execute(...)`` 结果的替身。"""

    def __init__(self, rows: Sequence[Tuple[str, str]]) -> None:
        self._rows: List[Tuple[str, str]] = list(rows)

    def all(self) -> List[Tuple[str, str]]:
        return self._rows


class _FakeDb:
    """最小概率 Session 替身（只有 ``execute``）。"""

    def __init__(self, rows: Sequence[Tuple[str, str]]) -> None:
        self._rows = rows

    def execute(self, _statement: Any) -> _FakeResult:
        return _FakeResult(self._rows)


class _BrokenDb:
    """execute 必炸的替身，用于验证降级路径。"""

    def execute(self, _statement: Any) -> _FakeResult:
        raise RuntimeError("boom")


def test_read_adaptive_config_reads_system_config():
    """``system_config`` 里的行能覆盖缺省值（key/value 都是字符串）。"""
    if not _sqlalchemy_available():
        return  # 离线纯单测环境跳过（不依赖 DB）
    rows = [
        ("adaptive.difficulty.band_width", "1.25"),
        ("adaptive.difficulty.inertia", "0"),
        ("adaptive.difficulty.enabled", "false"),
    ]
    config = read_adaptive_config(_FakeDb(rows))
    assert config.difficulty_band_width == 1.25
    assert config.difficulty_inertia == 0.0
    assert config.difficulty_enabled is False
    # 未配置的行保持缺省值
    assert config.difficulty_cold_start_samples == 3


def test_read_adaptive_config_falls_back_on_failure():
    """读库失败（表不存在/会话异常）一律回退缺省配置，不上抛。"""
    broken = read_adaptive_config(_BrokenDb())
    assert broken == DEFAULT_ADAPTIVE_CONFIG


def _placeholder_calls() -> List[Any]:
    """三个占位引擎的「最少实参调用」，便于统一断言它们会 loud fail。"""
    from app.engines import build_learning_plan, plan_exercises, rank_priorities

    return [
        lambda: rank_priorities({}),
        lambda: plan_exercises([], []),
        lambda: build_learning_plan([], []),
    ]


# ============================================================ 纯函数铁律
def _top_level_imports(module: Any) -> List[str]:
    """用 AST 取出模块的**顶层** import 目标（函数体内的延迟导入不算）。"""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(module))
    names: List[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


def test_engine_modules_have_no_orm_imports():
    """引擎本体顶层不得 import ORM / Session / os（保证能被纯单测直接 import）。"""
    from app.engines import config as config_module
    from app.engines import difficulty, types

    for module in (difficulty, types, config_module):
        imported = _top_level_imports(module)
        assert imported, f"{module.__name__} 解析失败"
        for name in imported:
            assert not name.startswith("sqlalchemy"), (module.__name__, name)
            assert not name.startswith("app.models"), (module.__name__, name)
            assert name != "os", (module.__name__, name)


def test_config_module_imports_orm_lazily():
    """``config.py`` 只有 ``read_adaptive_config`` 内部才 import ORM（藏在函数体）。"""
    import inspect

    from app.engines import config as config_module

    source = inspect.getsource(config_module)
    for line in source.splitlines():
        stripped = line.lstrip()
        is_import_line = stripped.startswith(("from ", "import "))
        mentions_orm = "sqlalchemy" in line or "models.system_config" in line
        if is_import_line and mentions_orm:
            # 只允许出现在函数体内（带缩进的延迟导入）
            assert line[:1] in (" ", "\t"), f"顶层 ORM 导入违反铁律: {line}"


def test_placeholder_engines_raise_not_implemented():
    """T06/T07 占位入口必须 loud fail（返回空结果比抛错更危险）。"""
    for call in _placeholder_calls():
        try:
            call()
        except NotImplementedError:
            continue
        raise AssertionError(f"{call} 占位实现应抛 NotImplementedError")


# ============================================================ standalone runner
def _run_all() -> int:
    """无 pytest 环境下的自检 runner：逐条执行本模块的 ``test_*`` 函数。"""
    current = globals()
    names = sorted(name for name in current if name.startswith("test_"))
    failed: List[str] = []
    for name in names:
        func = current[name]
        if not callable(func):
            continue
        try:
            func()
        except Exception as exc:  # noqa: BLE001 - 自检脚本要跑完全部用例
            failed.append(name)
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"[PASS] {name}")
    total = len(names)
    print("-" * 60)
    print(f"total={total} passed={total - len(failed)} failed={len(failed)}")
    if failed:
        print("failed cases: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_run_all())
