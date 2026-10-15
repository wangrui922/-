# -*- coding: utf-8 -*-
"""优先级引擎（V6.5 Sprint 6 · **T06 待实现**，本 Sprint 只留占位契约）。

职责边界
--------
在难度引擎之后、出题引擎之前介入：把学生 DNA 里的知识点掌握汇总排成一串
**学习优先级**（先补什么），答案为零/adaptive.mastery Wilson 口径变更后还要
把「前置缺口」顶上来（``MASTERY_GAP_THRESHOLD`` 见 ``core.constants``）。

输入 / 输出契约（T06 实现时必须遵守）
------------------------------------
* 入参：``dna``（``get_learning_dna`` 的返回值）+ ``config``（``AdaptiveConfig``
  里 ``priority_*`` 前缀的阈值）+ 可选的 ``limit``；
* 出参：``List[PriorityItem]``（见 ``app.engines.types``），按 ``score`` 降序；
* 纯函数铁律同 T05：禁止 ORM / Session / env，阈值一律由调用方注入。

⚠️ 当前调用会抛 ``NotImplementedError``——这是**故意**的：宁可 loud fail，
也不要返回一条谁也看不懂的默认排序。
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional

from app.engines.config import AdaptiveConfig
from app.engines.types import PriorityItem

#: 负责人任务号（便于后续接線时定位）
OWNER_TASK: str = "T06"


def rank_priorities(
    dna: Optional[Mapping[str, Any]] = None,
    *,
    config: Optional[AdaptiveConfig] = None,
    limit: int = 0,
) -> List[PriorityItem]:
    """按薄弱度/前置缺口排出学习优先级（**T06 实现**，当前占位）。

    Args:
        dna: ``get_learning_dna()`` 的返回值。
        config: 阈值配置（用到 ``priority_*`` 前缀字段）。
        limit: 返回条数上限；``0`` 表示不限制。

    Returns:
        ``List[PriorityItem]``，``score`` 降序。

    Raises:
        NotImplementedError: 恒抛出——占位实现，等 ``OWNER_TASK``（T06）落地。
    """
    raise NotImplementedError(
        f"优先级引擎尚未实现，请在 {OWNER_TASK}（V6.5 Sprint 6）补齐；"
        f"入参已按契约预留（dna={type(dna).__name__}, limit={limit}, "
        f"config={'default' if config is None else 'injected'}）"
    )


__all__ = ["OWNER_TASK", "rank_priorities"]
