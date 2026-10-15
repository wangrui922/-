# -*- coding: utf-8 -*-
"""出题引擎（V6.5 Sprint 6 · **T06 待实现**，本 Sprint 只留占位契约）。

职责边界
--------
拿到 T05 的难度带 + T06 的优先级条目后，决定「**出几道、每个难度档各几道**」——
它只做**配额与档位分配**，真正去题库捞题是 ``practice_service`` /
``ai_exercise_service`` 的事（引擎层不碰 ORM）。

输入 / 输出契约（T06 实现时必须遵守）
------------------------------------
* 入参：``PriorityItem`` + ``DifficultyBand`` + 计划题数 ``total`` +
  ``config``（``generation_*`` 前缀阈值）；
* 出参：``List[ExercisePlan]``（见 ``app.engines.types``）；
* 纯函数铁律同 T05：禁止 ORM / Session / env，阈值一律由调用方注入。

⚠️ 当前调用会抛 ``NotImplementedError``——宁可 loud fail，也不静默返回空题单。
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from app.engines.config import AdaptiveConfig
from app.engines.types import DifficultyBand, ExercisePlan, PriorityItem

#: 负责人任务号
OWNER_TASK: str = "T06"


def plan_exercises(
    priorities: Sequence[PriorityItem],
    bands: Sequence[DifficultyBand],
    *,
    total: int = 0,
    config: Optional[AdaptiveConfig] = None,
) -> List[ExercisePlan]:
    """把优先级 + 难度带翻译成出题配额（**T06 实现**，当前占位）。

    Args:
        priorities: 优先级引擎产出的知识点列表。
        bands: 与 ``priorities`` 一一对应的难度带（来自 T05 引擎）。
        total: 本次计划出题总数；``0`` 表示由引擎按知识点数自行摊派。
        config: 阈值配置（用到 ``generation_*`` 前缀字段）。

    Returns:
        ``List[ExercisePlan]``。

    Raises:
        NotImplementedError: 恒抛出——占位实现，等 ``OWNER_TASK``（T06）落地。
    """
    raise NotImplementedError(
        f"出题引擎尚未实现，请在 {OWNER_TASK}（V6.5 Sprint 6）补齐；"
        f"入参已按契约预留（{len(list(priorities))} 个优先级项、"
        f"{len(list(bands))} 条难度带、total={total}）"
    )


__all__ = ["OWNER_TASK", "plan_exercises"]
