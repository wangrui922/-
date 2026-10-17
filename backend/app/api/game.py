"""游戏化学习接口（方向六 · B7）。

端点：
    GET  /api/game/hub             游戏大厅（积分/徽章/最近记录）
    POST /api/game/speed/start     限时挑战开局
    POST /api/game/speed/answer    限时挑战逐题作答
    POST /api/game/speed/finish    限时挑战结算
    POST /api/game/battle/start    回合战斗开局
    POST /api/game/battle/answer   回合战斗逐题作答
    POST /api/game/battle/finish   回合战斗结算
    GET  /api/game/badges          徽章墙
    GET  /api/game/leaderboard     排行榜
    GET  /api/game/history         游戏历史

硬约定：
    * 需要登录且 ``user.sid`` 非空（``require_student_id`` 返 400）；
    * 全部 POST 路由挂 ``require_guardian_consent`` 监护人同意门控
      （``CONSENT_GATE_ENABLED`` 默认关，开启后无当前版本同意返 403）；
      GET 一律不挂（大厅 / 徽章墙 / 排行榜 / 历史保持可浏览）；
    * 出参一律 snake_case，由 ``app.core.response.ok()`` 包裹（C10）；
    * 纯本地计算，用 sync ``def`` 路由（FastAPI 自动丢线程池）；
    * 他人会话按 404 处理。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_guardian_consent
from app.core.response import ok
from app.db import get_db
from app.schemas.game import (
    BattleAnswerRequest,
    BattleFinishRequest,
    BattleStartRequest,
    BadgeItem,
    GameHistoryItem,
    HubResponse,
    LeaderboardItem,
    SpeedAnswerRequest,
    SpeedFinishRequest,
    SpeedStartRequest,
)
from app.services.game_service import (
    finish_math_battle,
    finish_speed_challenge,
    get_game_history,
    get_leaderboard,
    get_recommended_difficulty,
    get_student_badges,
    start_math_battle,
    start_speed_challenge,
    submit_battle_answer,
    submit_speed_answer,
)
from app.services.learning_service import require_student_id

logger = logging.getLogger("app.api.game")

router = APIRouter(prefix="/game", tags=["game"])


# ------------------------------------------------------------------ 游戏大厅

@router.get("/hub")
def game_hub(
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """游戏大厅：返回学生积分、徽章统计和最近游戏记录。"""
    student_id = require_student_id(current.sid)

    from app.models.gamification import StudentPoint
    from sqlalchemy import select

    point = db.execute(
        select(StudentPoint).where(StudentPoint.student_id == student_id)
    ).scalar_one_or_none()

    badges = get_student_badges(db, student_id)
    recent = get_game_history(db, student_id, limit=5)

    data = HubResponse(
        total_points=point.total_points if point else 0,
        weekly_points=point.weekly_points if point else 0,
        monthly_points=point.monthly_points if point else 0,
        speed_best_score=point.speed_best_score if point else 0,
        battle_best_wave=point.battle_best_wave if point else 0,
        total_games=point.total_games if point else 0,
        badge_count=len(badges),
        badge_total=15,
        recent_games=recent,
    )
    return ok(data.model_dump())


# ------------------------------------------------------------------ 限时挑战

@router.post("/speed/start", dependencies=[Depends(require_guardian_consent)])
def speed_start(
    body: SpeedStartRequest = Body(default_factory=SpeedStartRequest),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """限时挑战开局。"""
    student_id = require_student_id(current.sid)
    session, questions = start_speed_challenge(
        db,
        student_id=student_id,
        difficulty=body.difficulty,
        scenario_type=body.scenario_type,
    )
    return ok(
        {
            "game_session_id": str(session.id),
            "duration_sec": 60,
            "questions": questions,
        }
    )


@router.post("/speed/answer", dependencies=[Depends(require_guardian_consent)])
def speed_answer(
    body: SpeedAnswerRequest = Body(...),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """限时挑战逐题作答。"""
    require_student_id(current.sid)
    result = submit_speed_answer(
        db,
        game_session_id=uuid.UUID(body.game_session_id),
        question_id=uuid.UUID(body.question_id),
        student_answer=body.student_answer,
        elapsed_ms=body.elapsed_ms,
    )
    return ok(result)


@router.post("/speed/finish", dependencies=[Depends(require_guardian_consent)])
def speed_finish(
    body: SpeedFinishRequest = Body(...),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """限时挑战结算。"""
    require_student_id(current.sid)
    result = finish_speed_challenge(
        db, game_session_id=uuid.UUID(body.game_session_id)
    )
    return ok(
        {
            "score": result.score,
            "correct_count": result.correct_count,
            "wrong_count": result.wrong_count,
            "max_combo": result.max_combo,
            "duration_sec": result.duration_sec,
            "new_badges": [
                {
                    "badge_id": str(b.id),
                    "name": b.name,
                    "display_name": b.display_name,
                    "description": b.description,
                    "category": b.category,
                    "rarity": b.rarity,
                    "icon_url": b.icon_url,
                }
                for b in result.new_badges
            ],
            "point_summary": result.point_summary,
        }
    )


# ------------------------------------------------------------------ 回合战斗

@router.post("/battle/start", dependencies=[Depends(require_guardian_consent)])
def battle_start(
    body: BattleStartRequest = Body(default_factory=BattleStartRequest),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """回合战斗开局。"""
    student_id = require_student_id(current.sid)
    session, payload = start_math_battle(
        db, student_id=student_id, difficulty=body.difficulty
    )
    return ok(
        {
            "game_session_id": str(session.id),
            "player_hp": payload["player_hp"],
            "monster": payload["monster"],
            "question": payload["question"],
        }
    )


@router.post("/battle/answer", dependencies=[Depends(require_guardian_consent)])
def battle_answer(
    body: BattleAnswerRequest = Body(...),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """回合战斗逐题作答。"""
    require_student_id(current.sid)
    result = submit_battle_answer(
        db,
        game_session_id=uuid.UUID(body.game_session_id),
        question_id=uuid.UUID(body.question_id),
        student_answer=body.student_answer,
    )
    return ok(result)


@router.post("/battle/finish", dependencies=[Depends(require_guardian_consent)])
def battle_finish(
    body: BattleFinishRequest = Body(...),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """回合战斗结算。"""
    require_student_id(current.sid)
    result = finish_math_battle(
        db, game_session_id=uuid.UUID(body.game_session_id)
    )
    return ok(
        {
            "score": result.score,
            "correct_count": result.correct_count,
            "wrong_count": result.wrong_count,
            "best_wave": result.point_summary.get("battle_best_wave", 0),
            "duration_sec": result.duration_sec,
            "new_badges": [
                {
                    "badge_id": str(b.id),
                    "name": b.name,
                    "display_name": b.display_name,
                    "description": b.description,
                    "category": b.category,
                    "rarity": b.rarity,
                    "icon_url": b.icon_url,
                }
                for b in result.new_badges
            ],
            "point_summary": result.point_summary,
        }
    )


# ------------------------------------------------------------------ 徽章 / 排行榜 / 历史

@router.get("/badges")
def badges(
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """徽章墙。"""
    student_id = require_student_id(current.sid)
    rows = get_student_badges(db, student_id)
    return ok(rows)


@router.get("/leaderboard")
def leaderboard(
    rank_type: str = Query("weekly", regex="^(weekly|monthly)$"),
    period: Optional[str] = Query(None),
    top_n: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """排行榜（默认当前周期）。"""
    rows = get_leaderboard(db, rank_type=rank_type, period=period, top_n=top_n)
    return ok(rows)


@router.get("/history")
def history(
    game_type: Optional[str] = Query(None, regex="^(speed_challenge|math_battle)$"),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """游戏历史。"""
    student_id = require_student_id(current.sid)
    rows = get_game_history(db, student_id, game_type=game_type, limit=limit)
    return ok(rows)
