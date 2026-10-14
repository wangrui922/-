# -*- coding: utf-8 -*-
"""掌握度驱动抽题规划——P0·Phase3 纯算法层。

本模块只提供 ``plan_exercise_batch`` 纯函数，不接触 ORM/DB。
设计约束（对齐 mastery_engine.py）：
    * 输入全部是基础类型 / dataclass；
    * 输出可 JSON 序列化（不含 datetime 等复杂对象）；
    * 无随机性（按确定性排序），单测友好。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from app.services.mastery_engine import (
    MASTERY_GAP_THRESHOLD,
    MasteryResult,
    PrerequisiteEdge,
)


def _score_for_sort(result: MasteryResult) -> Tuple[float, int, float]:
    """排序键：掌握度升序 → 尝试次数升序（越没练越优先）→ 错误率降序。"""
    return (
        float(result.score),
        int(result.attempts),
        -float(result.error_rate),
    )


def plan_exercise_batch(
    mastery: Mapping[str, MasteryResult],
    edges: Sequence[PrerequisiteEdge],
    *,
    questions_by_kp: Mapping[str, Sequence[str]],
    count: int = 10,
    max_pre_ratio: float = 0.5,
    gap_threshold: float = MASTERY_GAP_THRESHOLD,
) -> Dict[str, Any]:
    """掌握度驱动抽题规划。

    策略：
        1. 在所有**有可用题目**的 L2 知识点中，按掌握度最低优先排序；
        2. 取最弱 L2，检查其前置依赖缺口（前置掌握度 < threshold 或零样本）；
        3. 若存在缺口：
           * 前置知识点有题 → 前置补漏题占 ``ceil(count * max_pre_ratio)``，
             剩余从目标 L2 抽；
           * 前置知识点无题 → 忽略缺口，全部从目标 L2 抽；
        4. 目标 L2 无题 → 按排序找下一个有题的 L2；
        5. 全部 L2 都无题 → 返回空列表（由上层回退）。

    Args:
        mastery: ``{kp_id(str): MasteryResult}``（L1+L2 双粒度）。
        edges: 前置依赖边序列。
        questions_by_kp: ``{kp_id: [qid, ...]}``，各知识点可用题目（已过滤掉不可归因/已回避等）。
        count: 请求题量（默认 10）。
        max_pre_ratio: 前置补漏题占比上限（默认 50%，即最多 5 道前置题）。
        gap_threshold: 前置缺口线（默认 0.55）。

    Returns:
        ``{"target_kp", "target_name", "pre_kp", "pre_name", "selected", "strategy", "plan_detail"}``。
        ``selected`` 为题 ID 列表，长度 <= count。
    """
    count = max(1, int(count))
    max_pre = max(0, int(round(count * max_pre_ratio)))

    # 1) 所有「有题」的知识点都纳入；无 mastery 数据视为默认零值（最弱）
    kp_with_pool: List[Tuple[str, MasteryResult]] = []
    all_kps = set(questions_by_kp.keys()) | set(mastery.keys())
    for kp_id in all_kps:
        pool = questions_by_kp.get(kp_id, ())
        if not pool:
            continue
        result = mastery.get(kp_id)
        if result is None:
            result = MasteryResult()
        kp_with_pool.append((kp_id, result))

    if not kp_with_pool:
        return {
            "target_kp": None,
            "target_name": None,
            "pre_kp": None,
            "pre_name": None,
            "selected": [],
            "strategy": "empty",
            "plan_detail": {"reason": "所有知识点均无可用题目"},
        }

    # 2) 按掌握度升序排序（最弱的优先）
    kp_with_pool.sort(key=lambda item: _score_for_sort(item[1]))

    # 3) 建前置边索引：后置 kp → [(前置 kp, weight), ...]
    edge_index: Dict[str, List[Tuple[str, float]]] = {}
    for edge in edges:
        edge_index.setdefault(edge.kp_id, []).append((edge.pre_kp_id, edge.weight))

    # 4) 逐候选 L2 找目标 + 前置
    for target_kp, target_result in kp_with_pool:
        target_pool = list(questions_by_kp.get(target_kp, ()))
        if not target_pool:
            continue

        # 找缺口前置（只取第一条， strongest / most severe）
        pre_candidates: List[Tuple[str, float, float, bool]] = []
        for pre_kp, weight in edge_index.get(target_kp, []):
            pre = mastery.get(pre_kp)
            if pre is not None and float(pre.score) >= gap_threshold and int(pre.attempts) > 0:
                continue  # 前置已达标
            pre_score = float(pre.score) if pre is not None else 0.0
            pre_attempts = int(pre.attempts) if pre is not None else 0
            pre_candidates.append((pre_kp, weight, pre_score, pre_attempts <= 0))

        if not pre_candidates:
            # 无缺口 → 全部从目标抽
            take = min(count, len(target_pool))
            return {
                "target_kp": target_kp,
                "target_name": None,
                "pre_kp": None,
                "pre_name": None,
                "selected": target_pool[:take],
                "strategy": "target",
                "plan_detail": {
                    "target_score": round(float(target_result.score), 4),
                    "target_attempts": int(target_result.attempts),
                    "target_pool_size": len(target_pool),
                    "taken": take,
                },
            }

        # 有缺口 → 取最强依赖且最严重的前置
        pre_candidates.sort(key=lambda t: (-t[1], t[2]))
        pre_kp, pre_weight, pre_score, pre_missing = pre_candidates[0]
        pre_pool = list(questions_by_kp.get(pre_kp, ()))

        if not pre_pool:
            # 前置无题 → 忽略缺口，全部从目标抽
            take = min(count, len(target_pool))
            return {
                "target_kp": target_kp,
                "target_name": None,
                "pre_kp": None,
                "pre_name": None,
                "selected": target_pool[:take],
                "strategy": "target",
                "plan_detail": {
                    "target_score": round(float(target_result.score), 4),
                    "target_attempts": int(target_result.attempts),
                    "target_pool_size": len(target_pool),
                    "taken": take,
                    "skipped_pre": pre_kp,
                    "skipped_reason": "前置知识点无可用题目",
                },
            }

        # 前置有题 → 混合策略
        pre_take = min(max_pre, len(pre_pool))
        target_take = min(count - pre_take, len(target_pool))
        selected = pre_pool[:pre_take] + target_pool[:target_take]
        return {
            "target_kp": target_kp,
            "target_name": None,
            "pre_kp": pre_kp,
            "pre_name": None,
            "selected": selected,
            "strategy": "mixed",
            "plan_detail": {
                "target_score": round(float(target_result.score), 4),
                "target_attempts": int(target_result.attempts),
                "target_pool_size": len(target_pool),
                "target_taken": target_take,
                "pre_score": round(pre_score, 4),
                "pre_missing": bool(pre_missing),
                "pre_pool_size": len(pre_pool),
                "pre_taken": pre_take,
                "pre_weight": round(pre_weight, 4),
            },
        }

    # 全部遍历完都无题（理论上已被 kp_with_pool 为空拦截）
    return {
        "target_kp": None,
        "target_name": None,
        "pre_kp": None,
        "pre_name": None,
        "selected": [],
        "strategy": "empty",
        "plan_detail": {"reason": "遍历全部候选后仍无可抽题目"},
    }
