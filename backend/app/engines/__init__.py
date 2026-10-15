# -*- coding: utf-8 -*-
"""V6.5 教学决策引擎层（Sprint 6）。

这里回答三个「AI 教练到底怎么教」的问题：

============  ================  ==================  ==============
入口函数       职责              数据类              实现状态
============  ================  ==================  ==============
``decide_difficulty``   下一批题出多难   ``DifficultyBand``  ✅ T05 已实现
``rank_priorities``     先补哪个知识点   ``PriorityItem``    ⏳ T06（占位）
``plan_exercises``      出几道、啥配比   ``ExercisePlan``    ⏳ T06（占位）
``build_learning_plan`` 多天日程怎么排   ``PlanItem``        ⏳ T07（占位）
============  ================  ==================  ==============

铁律（详见 ``docs/engines-design.md``）
--------------------------------------
* **引擎是纯函数**：``engines/difficulty.py`` / ``types.py`` 只依赖标准库，
  禁止 ORM / Session / env / 配置文件；阈值一律由调用方注入 ``AdaptiveConfig``；
* **唯一脏边界**是 ``config.read_adaptive_config(db)``（延迟 import ORM），
  服务层在请求入口调一次，之后所有引擎调用都是纯计算；
* 占位引擎调用时会 ``raise NotImplementedError``（loud fail，不静默兜底）。

接线顺序（建议）
----------------
``read_adaptive_config`` → ``get_learning_dna`` → ``difficulty_input_from_dna``
→ ``decide_difficulty`` → ``.to_api()`` → ``{code:0, msg:"ok", data:...}``。
"""

from __future__ import annotations

from app.engines.config import (
    CONFIG_KEYS,
    AdaptiveConfig,
    DEFAULT_ADAPTIVE_CONFIG,
    build_adaptive_config,
    read_adaptive_config,
)
# ⏳ T06：优先级引擎占位（调用即 NotImplementedError，见模块内说明）
from app.engines.generation import plan_exercises
from app.engines.priority import rank_priorities
from app.engines.difficulty import (
    decide_difficulty,
    decide_difficulty_payload,
    difficulty_input_from_dna,
)
# ⏳ T07：学习计划引擎占位
from app.engines.planner import build_learning_plan
from app.engines.types import (
    DEFAULT_DIFFICULTY,
    DIFFICULTY_LEVELS,
    DIFFICULTY_MAX,
    DIFFICULTY_MIN,
    DifficultyBand,
    DifficultyInput,
    ExercisePlan,
    PlanItem,
    PriorityItem,
    snap_levels,
)

__all__ = [
    # ---- T05 难度引擎（已实现）
    "decide_difficulty",
    "decide_difficulty_payload",
    "difficulty_input_from_dna",
    "DifficultyInput",
    "DifficultyBand",
    # ---- T06 优先级 / 出题（占位）
    "rank_priorities",
    "PriorityItem",
    "plan_exercises",
    "ExercisePlan",
    # ---- T07 学习计划（占位）
    "build_learning_plan",
    "PlanItem",
    # ---- 配置与工具
    "AdaptiveConfig",
    "DEFAULT_ADAPTIVE_CONFIG",
    "build_adaptive_config",
    "read_adaptive_config",
    "CONFIG_KEYS",
    "DIFFICULTY_LEVELS",
    "DIFFICULTY_MIN",
    "DIFFICULTY_MAX",
    "DEFAULT_DIFFICULTY",
    "snap_levels",
]
