"""自适应难度服务（#4 draw_adaptive）：纯算法 + 学生难度状态读写。

分层
----
* ``adjust_adaptive_state`` —— **纯函数（无 DB，可单测）**：窗口结算 + streak
  防抖 + 调档，输入/输出都是普通字段值；
* ``get_current_difficulty`` —— 读学生当前主难度（无记录回默认 3）；
* ``record_attempt_result`` —— 每次作答后更新窗口状态并落库（写库）；
* ``recommend_difficulty`` —— 抽题时的推荐难度（即当前主难度）。

算法口径
--------
* 窗口：每 ``WINDOW_SIZE``（默认 10）次作答结算一次；
* 高分窗口（正确率 ≥ ``UP_THRESHOLD``=0.8）：``up_streak += 1``，清 ``down_streak``；
* 低分窗口（正确率 ≤ ``DOWN_THRESHOLD``=0.4）：``down_streak += 1``，清 ``up_streak``；
* 中间档（0.4 < 正确率 < 0.8）：两个 streak 都清零——达标链被打断；
* streak 达到 ``MIN_STREAK``（默认 2）才真正调档（5→3→1 降 / 1→3→5 升），
  调档后 streak 与窗口一并清零——**防抖**，避免单次发挥波动反复跳档。

跨方言约束（对齐 practice_service C1）
-------------------------------------
不依赖任何数据库日期/随机函数，窗口结算与抽样全部在 Python 侧完成。

事务纪律
--------
* ``record_attempt_result`` 内部自查自 commit（幂等：无记录则先建默认行）；
* 调用方（practice_service / ai_exercise_service）在**判分已落库之后**再调本函数，
  本函数失败只影响难度推荐，绝不拖垮判分主流程（调用方须 try/except 兜底）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.adaptive import StudentAdaptiveLevel
from app.services.learning_service import to_uuid

logger = logging.getLogger("app.services.adaptive")

#: 难度档位（与 ``questions.difficulty`` 口径一致，DB 无 CHECK 约束但落库前收敛）
DIFFICULTIES: tuple[int, ...] = (1, 3, 5)
#: 新学生默认主难度
DEFAULT_DIFFICULTY: int = 3
#: 窗口大小：每 N 次作答结算一次
WINDOW_SIZE: int = 10
#: 高分阈值（正确率 ≥ 0.8 视为「这个难度稳了」）
UP_THRESHOLD: float = 0.8
#: 低分阈值（正确率 ≤ 0.4 视为「这个难度吃力了」）
DOWN_THRESHOLD: float = 0.4
#: 连续达标窗口数达到该值才真正调档（防抖）
MIN_STREAK: int = 2


# ============================================================ 纯算法（无 DB）
def _clamp_difficulty(value: Any) -> int:
    """把任意值收敛到合法档位；非法一律回默认 3。"""
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return DEFAULT_DIFFICULTY
    return raw if raw in DIFFICULTIES else DEFAULT_DIFFICULTY


def step_difficulty(difficulty: int, *, direction: int) -> int:
    """在档位序列 ``(1,3,5)`` 里向上/向下走一步（越界则停在边界）。

    Args:
        difficulty: 当前档位（1/3/5）。
        direction: ``+1`` 升档 / ``-1`` 降档。

    Returns:
        调整后的档位；已到边界则原地返回。
    """
    base = _clamp_difficulty(difficulty)
    index = DIFFICULTIES.index(base) + (1 if direction > 0 else -1)
    if index < 0:
        return DIFFICULTIES[0]
    if index >= len(DIFFICULTIES):
        return DIFFICULTIES[-1]
    return DIFFICULTIES[index]


def neighbor_difficulties(difficulty: int) -> list[int]:
    """返回当前档位之外的其余档位（不含自身），供自适应抽题补齐剩余题量。"""
    base = _clamp_difficulty(difficulty)
    return [d for d in DIFFICULTIES if d != base]


def adjust_adaptive_state(
    *,
    current_difficulty: Any = DEFAULT_DIFFICULTY,
    up_streak: int = 0,
    down_streak: int = 0,
    window_correct: int = 0,
    window_total: int = 0,
    is_correct: bool,
    window_size: int = WINDOW_SIZE,
    up_threshold: float = UP_THRESHOLD,
    down_threshold: float = DOWN_THRESHOLD,
    min_streak: int = MIN_STREAK,
) -> Dict[str, Any]:
    """窗口结算 + streak 防抖 + 调档（纯函数，可单测）。

    Args:
        current_difficulty: 当前主难度（1/3/5，非法回默认 3）。
        up_streak: 当前连续高分达标窗口数。
        down_streak: 当前连续低分达标窗口数。
        window_correct: 当前窗口累计答对数。
        window_total: 当前窗口累计答题数。
        is_correct: 本次作答是否判对。
        window_size: 窗口大小（满则结算，默认 10）。
        up_threshold: 高分阈值（默认 0.8）。
        down_threshold: 低分阈值（默认 0.4）。
        min_streak: 连续达标窗口数达到该值才调档（默认 2）。

    Returns:
        ``{"current_difficulty", "up_streak", "down_streak",
        "window_correct", "window_total", "adjusted"}``：
        ``adjusted`` 为 True 表示本次结算**真正调了档**（供日志/测试断言）。
    """
    base = _clamp_difficulty(current_difficulty)
    correct = max(0, int(window_correct)) + (1 if bool(is_correct) else 0)
    total = max(0, int(window_total)) + 1

    new_up = max(0, int(up_streak))
    new_down = max(0, int(down_streak))
    adjusted = False

    if total >= max(1, int(window_size)):
        rate = correct / total
        if rate >= up_threshold:
            new_up, new_down = new_up + 1, 0
        elif rate <= down_threshold:
            new_up, new_down = 0, new_down + 1
        else:
            # 中间档：达标链被打断，两个 streak 都清零
            new_up, new_down = 0, 0

        # 结算后窗口清零（窗口内计数只服务「结算」这一件事）
        correct, total = 0, 0

        if new_up >= max(1, int(min_streak)):
            base = step_difficulty(base, direction=1)
            new_up, new_down = 0, 0
            adjusted = True
        elif new_down >= max(1, int(min_streak)):
            base = step_difficulty(base, direction=-1)
            new_up, new_down = 0, 0
            adjusted = True

    return {
        "current_difficulty": base,
        "up_streak": new_up,
        "down_streak": new_down,
        "window_correct": correct,
        "window_total": total,
        "adjusted": adjusted,
    }


# ============================================================ 服务层（读写 DB）
def _load_or_create(db: Session, student_id: uuid.UUID) -> StudentAdaptiveLevel:
    """取学生的自适应状态行；不存在则建默认行（不 commit，交给调用方）。"""
    row = db.execute(
        select(StudentAdaptiveLevel).where(StudentAdaptiveLevel.student_id == student_id)
    ).scalar_one_or_none()
    if row is None:
        row = StudentAdaptiveLevel(
            student_id=student_id,
            current_difficulty=DEFAULT_DIFFICULTY,
        )
        db.add(row)
        db.flush()
    return row


def get_current_difficulty(db: Session, *, student_id: Any) -> int:
    """读学生当前主难度；无记录（新学生）返回默认 3。

    纯只读、不建行：抽题前调用不会产生副作用（避免预抽题写库）。
    """
    try:
        sid = to_uuid(student_id, "student_id")
    except Exception:  # noqa: BLE001 - 异常 ID 一律按默认难度处理
        return DEFAULT_DIFFICULTY
    row = db.execute(
        select(StudentAdaptiveLevel).where(StudentAdaptiveLevel.student_id == sid)
    ).scalar_one_or_none()
    if row is None:
        return DEFAULT_DIFFICULTY
    return _clamp_difficulty(row.current_difficulty)


def recommend_difficulty(db: Session, *, student_id: Any) -> int:
    """抽题推荐难度（自适应开关开启时的主难度档位）。"""
    return get_current_difficulty(db, student_id=student_id)


def record_attempt_result(db: Session, *, student_id: Any, is_correct: bool) -> Dict[str, Any]:
    """记录一次作答结果 → 更新窗口 / streak / 主难度并落库。

    幂等与失败隔离：内部自 commit；行不存在自动建默认行。调用方必须
    try/except 包一层——本函数失败只影响难度推荐，不影响判分主流程。

    Args:
        db: SQLAlchemy 会话。
        student_id: 学生 ID。
        is_correct: 本次作答是否判对。

    Returns:
        ``{"current_difficulty", "adjusted", "window_total"}``：
        ``adjusted`` 为 True 表示本次结算真正调了档。
    """
    sid = to_uuid(student_id, "student_id")
    row = _load_or_create(db, sid)

    next_state = adjust_adaptive_state(
        current_difficulty=row.current_difficulty,
        up_streak=row.up_streak,
        down_streak=row.down_streak,
        window_correct=row.window_correct,
        window_total=row.window_total,
        is_correct=is_correct,
    )

    changed = (
        row.current_difficulty != next_state["current_difficulty"]
        or row.up_streak != next_state["up_streak"]
        or row.down_streak != next_state["down_streak"]
        or row.window_correct != next_state["window_correct"]
        or row.window_total != next_state["window_total"]
    )
    if changed:
        row.current_difficulty = next_state["current_difficulty"]
        row.up_streak = next_state["up_streak"]
        row.down_streak = next_state["down_streak"]
        row.window_correct = next_state["window_correct"]
        row.window_total = next_state["window_total"]
        db.add(row)
        db.commit()

    if next_state["adjusted"]:
        logger.info(
            "自适应难度调档 student=%s → %s", sid, next_state["current_difficulty"]
        )

    return {
        "current_difficulty": next_state["current_difficulty"],
        "adjusted": next_state["adjusted"],
        "window_total": next_state["window_total"],
    }


__all__ = [
    "DIFFICULTIES",
    "DEFAULT_DIFFICULTY",
    "WINDOW_SIZE",
    "UP_THRESHOLD",
    "DOWN_THRESHOLD",
    "MIN_STREAK",
    "step_difficulty",
    "neighbor_difficulties",
    "adjust_adaptive_state",
    "get_current_difficulty",
    "recommend_difficulty",
    "record_attempt_result",
]