# -*- coding: utf-8 -*-
"""引擎层 · adaptive_config N1 golden 真值守卫（配置唯一真相源审计补齐）。

背景与目的
----------
``CONFIG_SPECS`` 是 31 个阈值的**唯一真相源**（backend/app/engines/config.py），
既有测试分散在 ``test_difficulty.py`` 里、只覆盖了 difficulty 前缀。本文件补齐：

1. **结构真值**：31 条 spec 的 key 前缀 / attr 与字段一一对应 / kind 合法 /
   default 与 ``AdaptiveConfig()`` 字段值一致（防加键不加档、防两套口径漂移）；
2. **全量覆盖真值**：一条用例把 31 个 key 全部设成互异值，逐字段写死解码结果；
3. **种子行真值**：``adaptive_config_to_rows`` 的序列化口径（bool→"true"/
   JSON→sort_keys）与 round-trip 等价；
4. **夹紧口径真值**：坐标上下界不对称夹紧、权重 JSON 的 clamp/bytes/缺 key 回退。

所有期望值按 ``config.py`` 定稿语义手工推导，不调用被测实现。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from app.engines.config import (
    CONFIG_KEY_PREFIX,
    CONFIG_KEYS,
    CONFIG_SPECS,
    DEFAULT_ADAPTIVE_CONFIG,
    AdaptiveConfig,
    ConfigSpec,
    adaptive_config_to_rows,
    build_adaptive_config,
    short_key,
)

#: 定稿的 spec 条数（团队口径：31 个键）
_EXPECTED_SPEC_COUNT: int = 31


# ============================================================ 结构真值
def test_config_specs_structure_golden():
    """CONFIG_SPECS 结构逐项写死：31 条、前缀统一、attr 唯一且是真实字段、
    kind 合法、default 与缺省配置字段值一致。"""
    assert len(CONFIG_SPECS) == _EXPECTED_SPEC_COUNT
    assert len(CONFIG_KEYS) == _EXPECTED_SPEC_COUNT
    assert len(set(CONFIG_KEYS)) == _EXPECTED_SPEC_COUNT  # key 不重复
    fields = set(AdaptiveConfig._field_names())
    seen_attrs: List[str] = []
    for spec in CONFIG_SPECS:
        assert isinstance(spec, ConfigSpec)
        assert spec.key.startswith(CONFIG_KEY_PREFIX), spec.key
        assert spec.key == CONFIG_KEY_PREFIX + short_key(spec.key)
        assert spec.attr in fields, spec.attr  # attr 必须是真实字段
        assert spec.kind in ("float", "int", "bool", "json"), spec.kind
        assert spec.attr not in seen_attrs, spec.attr  # attr 不重复
        seen_attrs.append(spec.attr)
        # default 与缺省配置的字段值一致（两套口径不得漂移）
        assert getattr(DEFAULT_ADAPTIVE_CONFIG, spec.attr) == spec.default, spec.attr


def test_config_specs_key_list_golden():
    """31 个完整 key 逐字写死（后台种子 / 运维脚本按这份清单核对）。"""
    assert CONFIG_KEYS == (
        "adaptive.enabled",
        "adaptive.difficulty.enabled",
        "adaptive.difficulty.min",
        "adaptive.difficulty.max",
        "adaptive.difficulty.default",
        "adaptive.difficulty.band_width",
        "adaptive.difficulty.confidence_width_gain",
        "adaptive.difficulty.inertia",
        "adaptive.difficulty.cold_start_samples",
        "adaptive.difficulty.cold_start_width",
        "adaptive.difficulty.error_rate_penalty",
        "adaptive.difficulty.accuracy_target",
        "adaptive.difficulty.accuracy_gain",
        "adaptive.difficulty.hint_high_offset",
        "adaptive.difficulty.hint_low_offset",
        "adaptive.difficulty.reason_shift",
        "adaptive.priority.weights",
        "adaptive.priority.gap_divisor",
        "adaptive.priority.out_degree_divisor",
        "adaptive.priority.recent_window",
        "adaptive.priority.stale_after",
        "adaptive.priority.top_n",
        "adaptive.priority.wrong_rate_floor",
        "adaptive.priority.wrong_boost",
        "adaptive.priority.wrong_saturation",
        "adaptive.priority.recent_attempts_cap",
        "adaptive.priority.saturation_penalty",
        "adaptive.generation.default_total",
        "adaptive.generation.max_total",
        "adaptive.generation.max_knowledge_points",
        "adaptive.generation.new_ratio",
    )


def test_short_key_strips_prefix_only():
    """short_key 只剥 adaptive. 前缀；无前缀原样返回。"""
    assert short_key("adaptive.difficulty.band_width") == "difficulty.band_width"
    assert short_key("difficulty.band_width") == "difficulty.band_width"
    assert short_key("adaptive.") == ""


# ============================================================ 全量覆盖真值
def test_all_thirty_one_keys_round_trip_golden():
    """31 个 key 全部设成互异合法值，逐字段写死解码结果（全量覆盖真值）。"""
    overrides: Dict[str, Any] = {
        "adaptive.enabled": "false",
        "adaptive.difficulty.enabled": "false",
        "adaptive.difficulty.min": "2",
        "adaptive.difficulty.max": "4",
        "adaptive.difficulty.default": "3.5",
        "adaptive.difficulty.band_width": "1.5",
        "adaptive.difficulty.confidence_width_gain": "0.25",
        "adaptive.difficulty.inertia": "0.9",
        "adaptive.difficulty.cold_start_samples": "8",
        "adaptive.difficulty.cold_start_width": "2.0",
        "adaptive.difficulty.error_rate_penalty": "0.4",
        "adaptive.difficulty.accuracy_target": "0.6",
        "adaptive.difficulty.accuracy_gain": "3.0",
        "adaptive.difficulty.hint_high_offset": "-1.0",
        "adaptive.difficulty.hint_low_offset": "0.5",
        "adaptive.difficulty.reason_shift": "1.0",
        "adaptive.priority.weights": '{"mastery": 0.1}',
        "adaptive.priority.gap_divisor": "4.0",
        "adaptive.priority.out_degree_divisor": "6",
        "adaptive.priority.recent_window": "3",
        "adaptive.priority.stale_after": "21",
        "adaptive.priority.top_n": "9",
        "adaptive.priority.wrong_rate_floor": "0.3",
        "adaptive.priority.wrong_boost": "0.5",
        "adaptive.priority.wrong_saturation": "5.0",
        "adaptive.priority.recent_attempts_cap": "15",
        "adaptive.priority.saturation_penalty": "0.7",
        "adaptive.generation.default_total": "8",
        "adaptive.generation.max_total": "12",
        "adaptive.generation.max_knowledge_points": "4",
        "adaptive.generation.new_ratio": "0.3",
    }
    assert len(overrides) == _EXPECTED_SPEC_COUNT
    config = build_adaptive_config(overrides)

    # difficulty 前缀
    assert config.enabled is False
    assert config.difficulty_enabled is False
    assert config.difficulty_min == 2.0
    assert config.difficulty_max == 4.0
    assert config.difficulty_default == 3.5  # clamp(3.5, 2, 4)
    assert config.difficulty_band_width == 1.5
    assert config.difficulty_confidence_width_gain == 0.25
    assert config.difficulty_inertia == 0.9
    assert config.difficulty_cold_start_samples == 8
    assert config.difficulty_cold_start_width == 2.0
    assert config.difficulty_error_rate_penalty == 0.4
    assert config.difficulty_accuracy_target == 0.6
    assert config.difficulty_accuracy_gain == 3.0
    assert config.difficulty_hint_high_offset == -1.0
    assert config.difficulty_hint_low_offset == 0.5
    assert config.difficulty_reason_shift == 1.0
    # priority 前缀（weights 缺 key 回退缺省：confidence/gap/degree）
    assert config.priority_weights == {
        "mastery": 0.1,
        "confidence": 0.25,
        "gap": 0.20,
        "degree": 0.15,
    }
    assert config.priority_gap_divisor == 4.0
    assert config.priority_out_degree_divisor == 6
    assert config.priority_recent_window == 3
    assert config.priority_stale_after == 21
    assert config.priority_top_n == 9
    assert config.priority_wrong_rate_floor == 0.3
    assert config.priority_wrong_boost == 0.5
    assert config.priority_wrong_saturation == 5.0
    assert config.priority_recent_attempts_cap == 15
    assert config.priority_saturation_penalty == 0.7
    # generation 前缀
    assert config.generation_default_total == 8
    assert config.generation_max_total == 12
    assert config.generation_max_knowledge_points == 4
    assert config.generation_new_ratio == 0.3


def test_short_keys_decode_identically():
    """短 key（无 adaptive. 前缀）与完整 key 解码结果逐字段一致。"""
    full = build_adaptive_config({"adaptive.difficulty.band_width": "1.25"})
    short = build_adaptive_config({"difficulty.band_width": "1.25"})
    assert full == short
    assert full.difficulty_band_width == 1.25


def test_empty_and_none_values_decode_to_defaults():
    """空字典 / None / 全 None 值 → 缺省配置（逐字段等于 DEFAULT）。"""
    assert build_adaptive_config({}) == DEFAULT_ADAPTIVE_CONFIG
    assert build_adaptive_config(None) == DEFAULT_ADAPTIVE_CONFIG
    all_none = build_adaptive_config({key: None for key in CONFIG_KEYS})
    assert all_none == DEFAULT_ADAPTIVE_CONFIG


def test_unknown_keys_are_ignored():
    """未知 key 直接忽略，不影响任何字段。"""
    mixed = build_adaptive_config(
        {
            "adaptive.unknown.key": "1",
            "difficulty.band_width": "2.0",
            "totally.unrelated": "x",
        }
    )
    assert mixed.difficulty_band_width == 2.0
    assert mixed == DEFAULT_ADAPTIVE_CONFIG.replace(difficulty_band_width=2.0)


# ============================================================ 种子行真值
def test_config_to_rows_serialization_golden():
    """种子行序列化口径写死：31 行、bool→"true"/"false"、JSON→sort_keys。"""
    rows = adaptive_config_to_rows()
    assert len(rows) == _EXPECTED_SPEC_COUNT
    assert set(rows) == set(CONFIG_KEYS)
    assert rows["adaptive.enabled"] == "true"
    assert rows["adaptive.difficulty.enabled"] == "true"
    assert rows["adaptive.difficulty.band_width"] == "0.5"
    assert rows["adaptive.difficulty.hint_high_offset"] == "-0.4"
    assert rows["adaptive.difficulty.cold_start_samples"] == "3"
    assert rows["adaptive.generation.new_ratio"] == "0.6"
    assert rows["adaptive.priority.wrong_rate_floor"] == "0.5"
    # JSON 权重：sort_keys + ensure_ascii=False
    assert rows["adaptive.priority.weights"] == json.dumps(
        {"mastery": 0.4, "confidence": 0.25, "gap": 0.2, "degree": 0.15},
        ensure_ascii=False,
        sort_keys=True,
    )


def test_rows_round_trip_to_default_config():
    """种子行 round-trip：``build(rows) == DEFAULT``（后台初始化不自相矛盾）。"""
    rows = adaptive_config_to_rows()
    assert build_adaptive_config(rows) == DEFAULT_ADAPTIVE_CONFIG


def test_false_boolean_rows_round_trip():
    """布尔关断的种子行 round-trip：两个开关都能被读回 False。"""
    rows = dict(adaptive_config_to_rows())
    rows["adaptive.enabled"] = "false"
    rows["adaptive.difficulty.enabled"] = "false"
    config = build_adaptive_config(rows)
    assert config.enabled is False
    assert config.difficulty_enabled is False
    assert config.difficulty_band_width == 0.5  # 其余不受影响


# ============================================================ 夹紧口径真值
def test_coordinate_bounds_clamp_asymmetry_golden():
    """坐标上下界夹紧不对称：min→[0,5]，max→[0,100]，default→[min,max]。

    手算：min=9 → 5；max=50 → 50；default = clamp(3, 5, 50) = 5。
    反向（min=2, max=9）：default = clamp(3, 2, 9) = 3，且 max 允许到 9
    （运营可以把坐标上界拉到 5 以上，min 不行）。
    """
    wide = build_adaptive_config(
        {"adaptive.difficulty.min": "9", "adaptive.difficulty.max": "50"}
    )
    assert (wide.difficulty_min, wide.difficulty_max, wide.difficulty_default) == (
        5.0,
        50.0,
        5.0,
    )
    raised = build_adaptive_config(
        {"adaptive.difficulty.min": "2", "adaptive.difficulty.max": "9"}
    )
    assert (raised.difficulty_min, raised.difficulty_max, raised.difficulty_default) == (
        2.0,
        9.0,
        3.0,
    )


def test_reversed_bounds_reset_everything():
    """上下界写反（max < min）→ 坐标三件套整体回退内置值。"""
    config = build_adaptive_config(
        {"adaptive.difficulty.min": "4", "adaptive.difficulty.max": "2"}
    )
    assert config.difficulty_min == 1.0
    assert config.difficulty_max == 5.0
    assert config.difficulty_default == 3.0


def test_zero_max_triggers_reversed_reset():
    """max=0 与缺省 min=1 构成 lo>hi → 同样整体回退（不是把 default 夹成 0）。"""
    config = build_adaptive_config({"adaptive.difficulty.max": "0"})
    assert config.difficulty_min == 1.0
    assert config.difficulty_max == 5.0
    assert config.difficulty_default == 3.0


def test_numeric_field_clamps_golden():
    """各数值字段夹紧口径逐项写死（负数回退 / 越界钳制 / 地板 1）。"""
    config = build_adaptive_config(
        {
            "adaptive.difficulty.band_width": "-5",
            "adaptive.difficulty.confidence_width_gain": "-1",
            "adaptive.difficulty.inertia": "9",
            "adaptive.difficulty.cold_start_samples": "-3",
            "adaptive.difficulty.cold_start_width": "-2",
            "adaptive.difficulty.error_rate_penalty": "-1",
            "adaptive.difficulty.accuracy_target": "9",
            "adaptive.difficulty.accuracy_gain": "-1",
            "adaptive.difficulty.reason_shift": "-1",
            "adaptive.priority.gap_divisor": "0",
            "adaptive.priority.out_degree_divisor": "-5",
            "adaptive.priority.recent_window": "-2",
            "adaptive.priority.stale_after": "-1",
            "adaptive.priority.top_n": "0",
            "adaptive.priority.wrong_rate_floor": "9",
            "adaptive.priority.wrong_boost": "9",
            "adaptive.priority.wrong_saturation": "-2",
            "adaptive.priority.recent_attempts_cap": "-4",
            "adaptive.priority.saturation_penalty": "-1",
            "adaptive.generation.default_total": "-9",
            "adaptive.generation.max_total": "0",
            "adaptive.generation.max_knowledge_points": "-9",
            "adaptive.generation.new_ratio": "9",
        }
    )
    # 非负字段：脏负值回退缺省
    assert config.difficulty_band_width == 0.5
    assert config.difficulty_confidence_width_gain == 0.5
    assert config.difficulty_cold_start_width == 1.0
    assert config.difficulty_error_rate_penalty == 0.8
    assert config.difficulty_accuracy_gain == 2.0
    assert config.difficulty_reason_shift == 0.5
    # [0,1] 钳制
    assert config.difficulty_inertia == 1.0
    assert config.difficulty_accuracy_target == 1.0
    assert config.priority_wrong_rate_floor == 1.0
    assert config.priority_wrong_boost == 1.0
    assert config.priority_saturation_penalty == 0.0
    assert config.generation_new_ratio == 1.0
    # 整数地板
    assert config.difficulty_cold_start_samples == 0  # max(0, …)
    assert config.priority_out_degree_divisor == 1  # max(1, …)
    assert config.priority_recent_window == 0
    assert config.priority_stale_after == 0
    assert config.priority_top_n == 1  # max(1, …)
    assert config.priority_recent_attempts_cap == 0
    assert config.generation_default_total == 1
    assert config.generation_max_total == 1
    assert config.generation_max_knowledge_points == 1
    # 正数除数：脏值回退缺省
    assert config.priority_gap_divisor == 3.0
    assert config.priority_wrong_saturation == 3.0


def test_dirty_strings_fall_back_to_defaults():
    """不可解析字符串 / 脏类型逐个字段回退缺省（不抛不飘）。"""
    config = build_adaptive_config(
        {
            "adaptive.difficulty.band_width": "abc",
            "adaptive.difficulty.cold_start_samples": "很多",
            "adaptive.difficulty.inertia": "",
            "adaptive.priority.weights": "{not json",
            "adaptive.priority.weights_typo": "1",
        }
    )
    assert config.difficulty_band_width == 0.5
    assert config.difficulty_cold_start_samples == 3
    assert config.difficulty_inertia == 0.3
    assert config.priority_weights == DEFAULT_ADAPTIVE_CONFIG.priority_weights


def test_weights_variants_golden():
    """优先级权重载体口径：dict / JSON 字符串走解析；bytes 与 JSON 数组回退缺省。

    ⚠️ bytes 口径说明：``_normalize_priority_weights`` 对 bytes 走
    ``json.loads(str(value))``，``str(b'…')`` 会带上 ``b'…'`` 前缀导致解析失败，
    因此 bytes **不在**「dict / JSON 字符串」契约内——按缺省值处理，
    本条 golden 把这个口径钉死（运营侧只应传 str）。
    """
    as_dict = build_adaptive_config({"adaptive.priority.weights": {"mastery": 0.9}})
    assert as_dict.priority_weights == {
        "mastery": 0.9,
        "confidence": 0.25,
        "gap": 0.2,
        "degree": 0.15,
    }
    as_json = build_adaptive_config(
        {"adaptive.priority.weights": '{"mastery": 0.9, "gap": 0.05}'}
    )
    assert as_json.priority_weights["mastery"] == 0.9
    assert as_json.priority_weights["gap"] == 0.05
    # bytes / JSON 数组（非映射）→ 全缺省
    as_bytes = build_adaptive_config({"adaptive.priority.weights": b'{"mastery": 0.7}'})
    assert as_bytes.priority_weights == DEFAULT_ADAPTIVE_CONFIG.priority_weights
    as_list = build_adaptive_config({"adaptive.priority.weights": "[1, 2, 3]"})
    assert as_list.priority_weights == DEFAULT_ADAPTIVE_CONFIG.priority_weights
    # 越界值被夹到 [0,1]
    clamped = build_adaptive_config({"adaptive.priority.weights": {"mastery": 5, "gap": -2}})
    assert clamped.priority_weights["mastery"] == 1.0
    assert clamped.priority_weights["gap"] == 0.0


def test_boolean_variants_golden():
    """布尔配置的识别口径：true/false/yes/no/on/off/1/0 + 大小写。"""
    truthy = ("true", "True", "YES", "y", "on", "1", 1, True)
    falsy = ("false", "FALSE", "no", "n", "off", "0", 0, False)
    for raw in truthy:
        assert build_adaptive_config({"adaptive.enabled": raw}).enabled is True, raw
    for raw in falsy:
        assert build_adaptive_config({"adaptive.enabled": raw}).enabled is False, raw
    # 识别不了 → 回退该 spec 的缺省（enabled 缺省 True）
    assert build_adaptive_config({"adaptive.enabled": "也许"}).enabled is True


def test_replace_is_pure_and_reclamps():
    """replace 不改原对象，且新对象重新走一遍夹紧（脏覆盖不得漏网）。"""
    original = DEFAULT_ADAPTIVE_CONFIG
    dirty = original.replace(difficulty_band_width=-9.0, priority_wrong_boost=9.0)
    assert dirty.difficulty_band_width == 0.5  # 回退缺省
    assert dirty.priority_wrong_boost == 1.0  # 钳到 1
    assert original.difficulty_band_width == 0.5
    assert original.priority_wrong_boost == 0.25
    clean = original.replace(difficulty_band_width=2.0)
    assert clean.difficulty_band_width == 2.0
    assert clean is not original


def test_frozen_config_rejects_mutation():
    """AdaptiveConfig 冻结：外部无法改脏共享配置。"""
    config = build_adaptive_config({"adaptive.difficulty.band_width": "1.0"})
    try:
        config.difficulty_band_width = 9.0  # type: ignore[misc]
    except Exception:  # noqa: BLE001 - FrozenInstanceError
        return
    raise AssertionError("AdaptiveConfig 应为冻结数据类")
