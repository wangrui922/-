# -*- coding: utf-8 -*-
"""``adaptive_config`` 的缺省值与解码器（V6.5 Sprint 6 · T05）。

配置载体
--------
**复用既有的 ``system_config`` 表**（``app.models.system_config.SystemConfig``），
key 一律以 ``adaptive.`` 打头（如 ``adaptive.difficulty.band_width``）。
不新建表、不加迁移——既躲开 PG/SQLite 双份 SQL，也让运营能在后台改阈值。

模块结构（刻意切成两半，守住「引擎是纯函数」的铁律）
------------------------------------------------
* **纯部分**（``AdaptiveConfig`` / ``DEFAULT_ADAPTIVE_CONFIG`` / ``build_adaptive_config``）：
  零 ORM 依赖，输入一个普通 ``Mapping`` 就吐一个冻结配置对象，同输入必同输出，
  可被单测直接覆盖；
* **脏边界**（``read_adaptive_config``）：全模块 **唯一** 碰 DB 的函数。ORM 在
  **函数体内延迟 import**，因此 ``import app.engines.config`` 不会拉起 sqlalchemy，
  纯单测/纯算法调用不受影响。引擎本体（``difficulty.py``）绝不调用它——配置由
  调用方在请求入口读一次后注入引擎。

用法
----
.. code-block:: python

    config = read_adaptive_config(db)                 # 服务层：读一次
    band = decide_difficulty(input_obj, config)       # 引擎层：纯计算
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from app.engines.types import (
    DIFFICULTY_MAX,
    DIFFICULTY_MIN,
    DEFAULT_DIFFICULTY,
    clamp,
    to_bool,
    to_float,
    to_int,
)

logger = logging.getLogger("app.engines.config")

#: 配置 key 统一前缀
CONFIG_KEY_PREFIX: str = "adaptive."

#: ``kind`` 取值
_KIND_FLOAT: str = "float"
_KIND_INT: str = "int"
_KIND_BOOL: str = "bool"
#: JSON 载体：value 可能是 dict，也可能是后台里存成的 JSON 字符串
_KIND_JSON: str = "json"

# ------------------------------------------------------------ 优先级缺省常量
# 与 ``engines.priority`` 的同名常量**同口径**（此处不 import 该模块，避免
# 与引擎形成循环依赖）；一致性由 tests/engines/test_priority.py 的守卫单测保证。
_PRIORITY_WEIGHTS_DEFAULT: Dict[str, float] = {
    "mastery": 0.40,
    "confidence": 0.25,
    "gap": 0.20,
    "degree": 0.15,
}
_PRIORITY_GAP_DIVISOR_DEFAULT: float = 3.0
_PRIORITY_OUTDEG_DIVISOR_DEFAULT: int = 5
_PRIORITY_RECENT_WINDOW_DEFAULT: int = 7
_PRIORITY_STALE_AFTER_DEFAULT: int = 14
_PRIORITY_TOP_N_DEFAULT: int = 5


def _non_negative(value: Any, default: float) -> float:
    """非负数值钳制（脏配置为负时回退缺省值）。"""
    number = to_float(value, default)
    return number if number >= 0.0 else float(default)


def _positive(value: Any, default: float) -> float:
    """正数钳制（脏配置为 0 / 负数时回退缺省值，避免除零）。"""
    number = to_float(value, default)
    return number if number > 0.0 else float(default)


def _normalize_priority_weights(
    value: Any, defaults: Optional[Mapping[str, float]] = None
) -> Dict[str, float]:
    """把优先级权重归一成完整 dict（缺 key / 解析失败回退缺省值）。

    支持两种载体：``dict`` 与 JSON 字符串（运营在后台按文本配置时很常见）。

    Args:
        value: 原始权重（dict / JSON 字符串 / 脏值）。
        defaults: 缺省权重表；``None`` 时用内置缺省表。

    Returns:
        四项俱全、每项都夹紧到 ``[0, 1]`` 的权重字典。
    """
    table: Mapping[str, float] = defaults or _PRIORITY_WEIGHTS_DEFAULT
    if isinstance(value, Mapping):
        source: Mapping[str, Any] = value
    elif isinstance(value, (str, bytes)):
        try:
            parsed = json.loads(str(value))
        except (TypeError, ValueError):
            parsed = None
        source = parsed if isinstance(parsed, Mapping) else {}
    else:
        source = {}
    return {
        name: clamp(to_float(source.get(name), default), 0.0, 1.0)
        for name, default in table.items()
    }


_CASTERS: Dict[str, Callable[[Any, Any], Any]] = {
    _KIND_FLOAT: lambda raw, default: to_float(raw, float(default)),
    _KIND_INT: lambda raw, default: to_int(raw, int(default)),
    _KIND_BOOL: lambda raw, default: to_bool(raw, bool(default)),
    _KIND_JSON: lambda raw, default: _normalize_priority_weights(
        raw, default if isinstance(default, Mapping) else None
    ),
}


@dataclass(frozen=True)
class ConfigSpec:
    """一条 adaptive 配置项的自描述（key ↔ 属性 ↔ 类型 ↔ 缺省值）。

    Attributes:
        key: ``system_config.key``，含 ``adaptive.`` 前缀。
        attr: ``AdaptiveConfig`` 上的字段名。
        kind: 目标类型（float / int / bool）。
        default: 缺省值（同时作为脏值兜底）。
    """

    key: str
    attr: str
    kind: str
    default: Any


@dataclass(frozen=True)
class AdaptiveConfig:
    """自适应教学的可调阈值集合（**冻结**，构造期即夹紧到合法区间）。

    字段命名规则：``difficulty_*`` 归难度引擎（T05），``priority_*`` /
    ``generation_*`` 归 T06——**各引擎只读自己的前缀**，新增引擎不要偷看
    别人的字段。（T07 编排层不引入新前缀：它只消费上述字段，阈值全部
    复用既有 key。）

    Attributes:
        enabled: 难度自适应总开关（关闭时难度带锁定为当前难度，宽度 0）。
        difficulty_enabled: 难度引擎开关（同上语义，粒度更细的一步 downgrade）。
        difficulty_min: 连续难度坐标下界。
        difficulty_max: 连续难度坐标上界。
        difficulty_default: 无数据时的默认主难度。
        difficulty_band_width: 难度带**半宽**（每侧），最终带宽 = 2 × 该值。
        difficulty_confidence_width_gain: 置信度补宽度：
            实际半宽 = ``band_width + (1 - confidence) * gain``（数据越少越探索）。
        difficulty_inertia: 推荐向当前难度回归的惯性权重 0~1
            （0 = 完全听掌握度，1 = 完全不动）。
        difficulty_cold_start_samples: 冷启动判定：练习次数低于该值即样本不足。
        difficulty_cold_start_width: 冷启动期的难度带半宽。
        difficulty_error_rate_penalty: 错误率惩罚系数（``center -= error_rate × 该值``）。
        difficulty_accuracy_target: 期望的近期正确率中心。
        difficulty_accuracy_gain: 近期正确率偏离中心的增益。
        difficulty_hint_high_offset: 提示依赖 high 的难度偏置（负=降难度）。
        difficulty_hint_low_offset: 提示依赖 low 的难度偏置（正=升难度）。
        difficulty_reason_shift: 判定「推上去/拉下来」的中心位移阈值。
        priority_weights: 优先级四项权重 ``{mastery, confidence, gap, degree}``，
            支持 dict 或 JSON 字符串，缺 key / 解析失败回退缺省权重。
        priority_gap_divisor: 前置缺口条数归一化除数（配置成 0 会回退缺省值）。
        priority_out_degree_divisor: 图谱出度归一化除数（至少为 1）。
        priority_recent_window: 近期练习窗口（天），窗口内练过才降权。
        priority_stale_after: 陈旧阈值（天），超过则按超出比例加分。
        priority_top_n: 缺省返回条数。
        priority_wrong_rate_floor: C1 触发门槛：近窗口错误率不低于该值才回炉。
        priority_wrong_boost: C1 错题加分上限（夹到 ``[0, 1]``，与 score 同值域）。
        priority_wrong_saturation: C1 加满分所需的错题数（除数，配置成 0 回退缺省）。
        priority_recent_attempts_cap: C2 单点饱和阈值（近窗口作答数超过即抑制）。
        priority_saturation_penalty: C2 抑制系数（置 1.0 即关闭该抑制）。
        generation_default_total: 未指定题数时的批次默认题数。
        generation_max_total: 单批题数硬上限。
        generation_max_knowledge_points: 单批最多覆盖的知识点数。
        generation_new_ratio: 新题占比，决定 ``new_first`` / ``review_first`` /
            ``balanced`` 策略。
    """

    enabled: bool = True
    difficulty_enabled: bool = True
    difficulty_min: float = DIFFICULTY_MIN
    difficulty_max: float = DIFFICULTY_MAX
    difficulty_default: float = DEFAULT_DIFFICULTY
    difficulty_band_width: float = 0.5
    difficulty_confidence_width_gain: float = 0.5
    difficulty_inertia: float = 0.3
    difficulty_cold_start_samples: int = 3
    difficulty_cold_start_width: float = 1.0
    difficulty_error_rate_penalty: float = 0.8
    difficulty_accuracy_target: float = 0.75
    difficulty_accuracy_gain: float = 2.0
    difficulty_hint_high_offset: float = -0.4
    difficulty_hint_low_offset: float = 0.2
    difficulty_reason_shift: float = 0.5
    priority_weights: Any = None
    priority_gap_divisor: float = 3.0
    priority_out_degree_divisor: int = 5
    priority_recent_window: int = 7
    priority_stale_after: int = 14
    priority_top_n: int = 5
    priority_wrong_rate_floor: float = 0.5
    priority_wrong_boost: float = 0.25
    priority_wrong_saturation: float = 3.0
    priority_recent_attempts_cap: int = 10
    priority_saturation_penalty: float = 0.85
    generation_default_total: int = 5
    generation_max_total: int = 20
    generation_max_knowledge_points: int = 3
    generation_new_ratio: float = 0.6

    def __post_init__(self) -> None:
        """把每个字段夹紧到合法区间（脏配置不得把推荐算飞）。"""
        set_field = object.__setattr__

        lower = clamp(self.difficulty_min, 0.0, DIFFICULTY_MAX)
        upper = clamp(self.difficulty_max, 0.0, 100.0)
        default_level = clamp(self.difficulty_default, lower, upper)
        if upper < lower:
            # 运营把上下界写反了：整体回退内置坐标，避免产出 lo > hi 的脏组合
            lower, upper = DIFFICULTY_MIN, DIFFICULTY_MAX
            default_level = DEFAULT_DIFFICULTY

        normalized: Dict[str, Any] = {
            "enabled": to_bool(self.enabled, True),
            "difficulty_enabled": to_bool(self.difficulty_enabled, True),
            "difficulty_min": lower,
            "difficulty_max": upper,
            "difficulty_default": default_level,
            "difficulty_band_width": _non_negative(self.difficulty_band_width, 0.5),
            "difficulty_confidence_width_gain": _non_negative(
                self.difficulty_confidence_width_gain, 0.5
            ),
            "difficulty_inertia": clamp(self.difficulty_inertia, 0.0, 1.0),
            "difficulty_cold_start_samples": max(
                0, to_int(self.difficulty_cold_start_samples, 3)
            ),
            "difficulty_cold_start_width": _non_negative(self.difficulty_cold_start_width, 1.0),
            "difficulty_error_rate_penalty": _non_negative(
                self.difficulty_error_rate_penalty, 0.8
            ),
            "difficulty_accuracy_target": clamp(self.difficulty_accuracy_target, 0.0, 1.0),
            "difficulty_accuracy_gain": _non_negative(self.difficulty_accuracy_gain, 2.0),
            "difficulty_hint_high_offset": to_float(self.difficulty_hint_high_offset, -0.4),
            "difficulty_hint_low_offset": to_float(self.difficulty_hint_low_offset, 0.2),
            "difficulty_reason_shift": _non_negative(self.difficulty_reason_shift, 0.5),
            "priority_weights": _normalize_priority_weights(self.priority_weights),
            "priority_gap_divisor": _positive(
                self.priority_gap_divisor, _PRIORITY_GAP_DIVISOR_DEFAULT
            ),
            "priority_out_degree_divisor": max(
                1,
                to_int(self.priority_out_degree_divisor, _PRIORITY_OUTDEG_DIVISOR_DEFAULT),
            ),
            "priority_recent_window": max(
                0, to_int(self.priority_recent_window, _PRIORITY_RECENT_WINDOW_DEFAULT)
            ),
            "priority_stale_after": max(
                0, to_int(self.priority_stale_after, _PRIORITY_STALE_AFTER_DEFAULT)
            ),
            "priority_top_n": max(1, to_int(self.priority_top_n, _PRIORITY_TOP_N_DEFAULT)),
            "priority_wrong_rate_floor": clamp(
                self.priority_wrong_rate_floor, 0.0, 1.0
            ),
            # 与 score 同值域：封顶避免所有错题点被拉平并列（见 engines/priority.py）
            "priority_wrong_boost": clamp(self.priority_wrong_boost, 0.0, 1.0),
            "priority_wrong_saturation": _positive(
                self.priority_wrong_saturation, 3.0
            ),
            "priority_recent_attempts_cap": max(
                0, to_int(self.priority_recent_attempts_cap, 10)
            ),
            "priority_saturation_penalty": clamp(
                self.priority_saturation_penalty, 0.0, 1.0
            ),
            "generation_default_total": max(
                1, to_int(self.generation_default_total, 5)
            ),
            "generation_new_ratio": clamp(self.generation_new_ratio, 0.0, 1.0),
            "generation_max_total": max(1, to_int(self.generation_max_total, 20)),
            "generation_max_knowledge_points": max(
                1, to_int(self.generation_max_knowledge_points, 3)
            ),
        }
        for name, value in normalized.items():
            set_field(self, name, value)

    def replace(self, **overrides: Any) -> "AdaptiveConfig":
        """基于当前配置做一次覆盖，返回一个新的冻结对象（不改动原对象）。"""
        payload: Dict[str, Any] = {}
        for name in self._field_names():
            if name in overrides:
                payload[name] = overrides[name]
            else:
                payload[name] = getattr(self, name)
        return AdaptiveConfig(**payload)

    @classmethod
    def _field_names(cls) -> Tuple[str, ...]:
        """列出全部配置字段名（供 ``replace`` / 文档自检使用）。"""
        return tuple(cls.__dataclass_fields__.keys())


#: 配置清单（唯一真相源：新增阈值只需在这里加一行）
#: 每项形如 ``(key 后缀, AdaptiveConfig 字段名, 类型, 缺省值)``，
#: 完整 key 由 ``CONFIG_KEY_PREFIX + 后缀`` 拼出。
_SPEC_TABLE: Tuple[Tuple[str, str, str, Any], ...] = (
    ("enabled", "enabled", _KIND_BOOL, True),
    ("difficulty.enabled", "difficulty_enabled", _KIND_BOOL, True),
    ("difficulty.min", "difficulty_min", _KIND_FLOAT, DIFFICULTY_MIN),
    ("difficulty.max", "difficulty_max", _KIND_FLOAT, DIFFICULTY_MAX),
    ("difficulty.default", "difficulty_default", _KIND_FLOAT, DEFAULT_DIFFICULTY),
    ("difficulty.band_width", "difficulty_band_width", _KIND_FLOAT, 0.5),
    ("difficulty.confidence_width_gain", "difficulty_confidence_width_gain", _KIND_FLOAT, 0.5),
    ("difficulty.inertia", "difficulty_inertia", _KIND_FLOAT, 0.3),
    ("difficulty.cold_start_samples", "difficulty_cold_start_samples", _KIND_INT, 3),
    ("difficulty.cold_start_width", "difficulty_cold_start_width", _KIND_FLOAT, 1.0),
    ("difficulty.error_rate_penalty", "difficulty_error_rate_penalty", _KIND_FLOAT, 0.8),
    ("difficulty.accuracy_target", "difficulty_accuracy_target", _KIND_FLOAT, 0.75),
    ("difficulty.accuracy_gain", "difficulty_accuracy_gain", _KIND_FLOAT, 2.0),
    ("difficulty.hint_high_offset", "difficulty_hint_high_offset", _KIND_FLOAT, -0.4),
    ("difficulty.hint_low_offset", "difficulty_hint_low_offset", _KIND_FLOAT, 0.2),
    ("difficulty.reason_shift", "difficulty_reason_shift", _KIND_FLOAT, 0.5),
    (
        "priority.weights",
        "priority_weights",
        _KIND_JSON,
        _PRIORITY_WEIGHTS_DEFAULT,
    ),
    (
        "priority.gap_divisor",
        "priority_gap_divisor",
        _KIND_FLOAT,
        _PRIORITY_GAP_DIVISOR_DEFAULT,
    ),
    (
        "priority.out_degree_divisor",
        "priority_out_degree_divisor",
        _KIND_INT,
        _PRIORITY_OUTDEG_DIVISOR_DEFAULT,
    ),
    (
        "priority.recent_window",
        "priority_recent_window",
        _KIND_INT,
        _PRIORITY_RECENT_WINDOW_DEFAULT,
    ),
    (
        "priority.stale_after",
        "priority_stale_after",
        _KIND_INT,
        _PRIORITY_STALE_AFTER_DEFAULT,
    ),
    ("priority.top_n", "priority_top_n", _KIND_INT, _PRIORITY_TOP_N_DEFAULT),
    (
        "priority.wrong_rate_floor",
        "priority_wrong_rate_floor",
        _KIND_FLOAT,
        0.5,
    ),
    ("priority.wrong_boost", "priority_wrong_boost", _KIND_FLOAT, 0.25),
    (
        "priority.wrong_saturation",
        "priority_wrong_saturation",
        _KIND_FLOAT,
        3.0,
    ),
    (
        "priority.recent_attempts_cap",
        "priority_recent_attempts_cap",
        _KIND_INT,
        10,
    ),
    (
        "priority.saturation_penalty",
        "priority_saturation_penalty",
        _KIND_FLOAT,
        0.85,
    ),
    ("generation.default_total", "generation_default_total", _KIND_INT, 5),
    ("generation.max_total", "generation_max_total", _KIND_INT, 20),
    (
        "generation.max_knowledge_points",
        "generation_max_knowledge_points",
        _KIND_INT,
        3,
    ),
    ("generation.new_ratio", "generation_new_ratio", _KIND_FLOAT, 0.6),
)

#: 展开后的配置清单（供 ``build_adaptive_config`` / ``adaptive_config_to_rows`` 使用）
CONFIG_SPECS: Tuple[ConfigSpec, ...] = tuple(
    ConfigSpec(CONFIG_KEY_PREFIX + suffix, attr, kind, default)
    for suffix, attr, kind, default in _SPEC_TABLE
)

#: 全部配置 key（``read_adaptive_config`` 只查这些行）
CONFIG_KEYS: Tuple[str, ...] = tuple(spec.key for spec in CONFIG_SPECS)

#: 缺省配置（引擎单测 / 降级路径直接用它，不需要 DB）
DEFAULT_ADAPTIVE_CONFIG: AdaptiveConfig = AdaptiveConfig()


def short_key(key: str) -> str:
    """去掉 ``adaptive.`` 前缀后的短 key（如 ``difficulty.band_width``）。"""
    return key[len(CONFIG_KEY_PREFIX):] if key.startswith(CONFIG_KEY_PREFIX) else key


def build_adaptive_config(values: Optional[Mapping[str, Any]] = None) -> AdaptiveConfig:
    """由原始 key-value 解码出一个冻结的 ``AdaptiveConfig``（**纯函数**）。

    兼容两种 key 写法：完整 ``adaptive.difficulty.band_width`` 与短写法
    ``difficulty.band_width``；脏值（不可解析、类型不对）自动回退缺省值，
    未知 key 直接忽略。

    Args:
        values: 原始配置字典（value 多为字符串，也可以是已转型的数值）。

    Returns:
        字段全部经过合法性夹紧的 ``AdaptiveConfig``。
    """
    source: Mapping[str, Any] = values if isinstance(values, Mapping) else {}
    payload: Dict[str, Any] = {}

    for spec in CONFIG_SPECS:
        raw = source.get(spec.key)
        if raw is None:
            raw = source.get(short_key(spec.key))
        if raw is None:
            payload[spec.attr] = spec.default
            continue
        caster = _CASTERS.get(spec.kind, _CASTERS[_KIND_FLOAT])
        payload[spec.attr] = caster(raw, spec.default)

    return AdaptiveConfig(**payload)


def adaptive_config_to_rows() -> Dict[str, str]:
    """把缺省配置序列化成 ``{key: value_str}``，用于种子/后台初始化。"""
    rows: Dict[str, str] = {}
    for spec in CONFIG_SPECS:
        value = getattr(DEFAULT_ADAPTIVE_CONFIG, spec.attr)
        if spec.kind == _KIND_JSON:
            rows[spec.key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        elif value is True or value is False:
            rows[spec.key] = "true" if value else "false"
        else:
            rows[spec.key] = str(value)
    return rows


def read_adaptive_config(db: Any) -> AdaptiveConfig:
    """从 ``system_config`` 表读出 adaptive 配置（**全模块唯一的 DB 边界**）。

    只读 CONFIG_KEYS 这些行；读不到/表不存在/会话异常一律记日志并回退缺省值——
    配置读失败绝不能把出题主流程打挂（与既有 practice_service 旁路兜底同思路）。

    Args:
        db: SQLAlchemy 会话（刻意标成 ``Any``，避免本模块 import ORM）。

    Returns:
        组装好的 ``AdaptiveConfig``；任何异常情况下都返回不带脏值的合法配置。
    """
    values: Dict[str, Any] = {}
    try:
        from sqlalchemy import select  # noqa: PLC0415 - 延迟导入，保持 import 期纯净
        from app.models.system_config import SystemConfig  # noqa: PLC0415

        rows = db.execute(
            select(SystemConfig.key, SystemConfig.value).where(SystemConfig.key.in_(CONFIG_KEYS))
        ).all()
        values = {str(key): value for key, value in rows if key is not None}
    except Exception as exc:  # noqa: BLE001 - 配置读失败按降级处理，不上抛
        logger.warning("读取 adaptive_config 失败，回退缺省值: %s", exc)
        values = {}

    return build_adaptive_config(values)


__all__ = [
    "CONFIG_KEY_PREFIX",
    "CONFIG_SPECS",
    "CONFIG_KEYS",
    "ConfigSpec",
    "AdaptiveConfig",
    "DEFAULT_ADAPTIVE_CONFIG",
    "build_adaptive_config",
    "adaptive_config_to_rows",
    "read_adaptive_config",
    "short_key",
]
