# -*- coding: utf-8 -*-
"""学习计划引擎（V6.5 Sprint 6 · **T07 待实现**，本 Sprint 只留占位契约）。

职责边界
--------
把 T06 的优先级列表铺成**多天日程**（每天练什么知识点、练多久、难度区间多少），
要考虑每日时长上限、复习卡混排、周末加量等运营口径。

输入 / 输出契约（T07 实现时必须遵守）
------------------------------------
* 入参：``List[PriorityItem]`` + ``ExercisePlan`` 列表 + 可用天数/每日分钟
  + ``config``（``plan_*`` 前缀阈值）；
* 出参：``List[PlanItem]``（见 ``app.engines.types``），按 ``day_index`` 升序；
* 纯函数铁律同 T05：禁止 ORM / Session / env，阈值一律由调用方注入。

⚠️ 当前调用会抛 ``NotImplementedError``——宁可 loud fail，也不要产出假计划。
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from app.engines.config import AdaptiveConfig
from app.engines.types import ExercisePlan, PlanItem, PriorityItem

#: 负责人任务号
OWNER_TASK: str = "T07"


def build_learning_plan(
    priorities: Sequence[PriorityItem],
    exercises: Sequence[ExercisePlan],
    *,
    days: int = 7,
    minutes_per_day: int = 30,
    config: Optional[AdaptiveConfig] = None,
) -> List[PlanItem]:
    """生成多天学习计划（**T07 实现**，当前占位）。

    Args:
        priorities: 优先级引擎产出的知识点列表。
        exercises: 出题引擎产出的配额列表。
        days: 计划天数；小于 1 时按 1 天处理。
        minutes_per_day: 每日建议投入分钟数。
        config: 阈值配置（用到 ``plan_*`` 前缀字段）。

    Returns:
        ``List[PlanItem]``，按 ``day_index`` 升序。

    Raises:
        NotImplementedError: 恒抛出——占位实现，等 ``OWNER_TASK``（T07）落地。
    """
    raise NotImplementedError(
        f"学习计划引擎尚未实现，请在 {OWNER_TASK}（V6.5 Sprint 6）补齐；"
        f"入参已按契约预留（{len(list(priorities))} 个优先级项、"
        f"{len(list(exercises))} 条出题配额、days={days}、minutes_per_day={minutes_per_day}）"
    )


__all__ = ["OWNER_TASK", "build_learning_plan"]
