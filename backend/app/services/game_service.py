"""游戏化学习业务服务（方向六 · B7）。

职责（全部同步函数，sync 路由可直接调）：
* ``start_speed_challenge``  限时挑战开局（抽题、建 session）；
* ``submit_speed_answer``    逐题判分 + 连击/积分实时累计；
* ``finish_speed_challenge`` 结算：总积分、徽章判定、错题入本、积分表更新；
* ``start_math_battle``      回合战斗开局（按波次抽题、怪物配置）；
* ``submit_battle_answer``   单题判分 → 胜则进下一波 / 败则扣血；
* ``finish_math_battle``     结算：通关波次、徽章判定、错题入本、积分表更新；
* ``award_badge``            徽章发放（幂等：已存在则跳过）；
* ``refresh_leaderboard``    周/月榜快照刷新（供定时任务调用）；
* ``get_recommended_difficulty`` 结合 GKT 掌握度返回 L1-L3 推荐难度。

硬约束落地
----------
* **C1 跨方言**：全部标准 SQL；时间窗口在 Python 侧算好再下发；
  随机抽题在 Python 侧 ``random.sample``，不依赖 ``ORDER BY RANDOM()``。
* **C2 幂等**：``award_badge`` 走 ``student_badges`` 的 UNIQUE(student_id,badge_id)；
  ``GameSession`` 的创建与结束分离，避免重复结算。
* **C4 错题入本**：答错题在 finish 时调用 ``learning_service.record_mistake``（幂等）。
* **C7 判分锁 L1**：逐题判分走 ``judge_engine.judge()``，仅支持「选择」「判断」。
* **C9 复用不新建**：练习会话复用 ``LearningTask``（task_type='daily'），
  游戏数据靠 ``game_sessions`` 独立记录，不侵入既有学习域表结构。

难度联动（GKT）
--------------
``get_recommended_difficulty`` 读取 ``student_mastery`` 的平均掌握度：
  * < 0.4 → L1（基础）
  * 0.4 ~ 0.7 → L2（进阶）
  * > 0.7 → L3（挑战）
学生首次游戏（无 mastery 数据）默认 L1。

⚠️ 跨域依赖：
  * 抽题走 ``practice_service._candidate_rows``（已支持 grade/difficulty 筛选）；
  * 判分走 ``judge_engine.judge()``；
  * 错题入库走 ``learning_service.record_mistake()``。
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.response import (
    ApiError,
    CODE_BAD_REQUEST,
    CODE_NOT_FOUND,
)
from app.models.base import utcnow, uuid_pk
from app.models.content import Question
from app.models.gamification import (
    GAME_TYPE_VALUES,
    Badge,
    GameSession,
    Leaderboard,
    StudentBadge,
    StudentPoint,
)
from app.models.learning import Attempt, LearningTask, Mistake
from app.models.user import Student
from app.services import judge_engine, learning_service

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ 常量

#: 限时挑战默认时长（秒）
SPEED_CHALLENGE_DURATION: int = 60
#: 限时挑战每题基础分
SPEED_BASE_SCORE: int = 10
#: 限时挑战连击加成系数（每连击 +5 分）
SPEED_COMBO_BONUS: int = 5
#: 限时挑战速度奖励阈值（秒）
SPEED_FAST_THRESHOLD: float = 3.0
#: 限时挑战速度奖励分
SPEED_FAST_BONUS: int = 5
#: 限时挑战抽题数量
SPEED_QUESTION_COUNT: int = 20

#: 回合战斗总波次
BATTLE_TOTAL_WAVES: int = 10
#: 回合战斗每波基础通关分
BATTLE_WAVE_BASE_SCORE: int = 20
#: 精英波次（波 5）额外奖励
BATTLE_ELITE_BONUS: int = 30
#: Boss 波次（波 10）额外奖励
BATTLE_BOSS_BONUS: int = 100
#: 玩家初始 HP
BATTLE_PLAYER_HP: int = 100
#: 玩家初始攻击（伤害=答对=怪物扣血）
BATTLE_PLAYER_ATK: int = 20
#: 难度攻击加成（L1=0, L2=+5, L3=+10）
BATTLE_DIFF_ATK_BONUS: Dict[int, int] = {1: 0, 2: 5, 3: 10}
#: 每波怪物攻击增量（基础 10 + 波次×2）
BATTLE_MONSTER_ATK_BASE: int = 10
BATTLE_MONSTER_ATK_PER_WAVE: int = 2
#: 精英波次额外攻击
BATTLE_ELITE_ATK_BONUS: int = 6
#: Boss 波次额外攻击
BATTLE_BOSS_ATK_BONUS: int = 10

#: 徽章稀有度积分权重（用于前端展示排序）
BADGE_RARITY_WEIGHT: Dict[str, int] = {
    "common": 1,
    "rare": 2,
    "epic": 3,
    "legendary": 4,
}

#: 排行榜周榜周期标识前缀
RANK_WEEK_PREFIX: str = "W"
#: 排行榜月榜周期标识前缀
RANK_MONTH_PREFIX: str = "M"


# ------------------------------------------------------------------ 数据结构

@dataclass
class SpeedChallengeState:
    """限时挑战的运行时状态（内存/Redis 缓存用，不进数据库）。"""

    student_id: uuid.UUID
    difficulty: int
    questions: List[Question] = field(default_factory=list)
    current_index: int = 0
    score: int = 0
    correct_count: int = 0
    wrong_count: int = 0
    combo: int = 0
    max_combo: int = 0
    start_time: Optional[datetime] = None
    answers: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class BattleState:
    """回合战斗的运行时状态。"""

    student_id: uuid.UUID
    difficulty: int
    questions: List[Question] = field(default_factory=list)
    current_wave: int = 0
    player_hp: int = BATTLE_PLAYER_HP
    score: int = 0
    correct_count: int = 0
    wrong_count: int = 0
    start_time: Optional[datetime] = None
    answers: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class GameResult:
    """游戏结算结果，供 API 响应。"""

    score: int
    correct_count: int
    wrong_count: int
    max_combo: int
    duration_sec: int
    new_badges: List[Badge]
    point_summary: Dict[str, int]


# ------------------------------------------------------------------ 难度联动（GKT）

def get_recommended_difficulty(db: Session, student_id: uuid.UUID) -> int:
    """结合 GKT 掌握度返回推荐难度 L1-L3。

    读取 ``student_mastery`` 的平均掌握度：
      * < 0.4 → L1
      * 0.4 ~ 0.7 → L2
      * > 0.7 → L3
    无数据默认 L1。
    """
    from app.models.student_mastery import StudentMastery

    rows = db.execute(
        select(func.avg(StudentMastery.mastery_score)).where(
            StudentMastery.student_id == student_id
        )
    ).scalar()
    avg_score = rows or 0.0
    if avg_score < 0.4:
        return 1
    if avg_score <= 0.7:
        return 2
    return 3


# ------------------------------------------------------------------ 抽题辅助

def _draw_questions_for_game(
    db: Session,
    student_id: uuid.UUID,
    difficulty: int,
    count: int,
    scenario_type: Optional[str] = None,
) -> List[Question]:
    """为游戏模式抽题（简化版：按 grade + difficulty 直接捞）。

    ⚠️ 与 ``practice_service._candidate_rows`` 的区别：
      * 不强制要求 kp_verified=1（游戏题可适当放宽，保证题库充足）；
      * 支持按 scenario_type 筛选（生活场景题）。
    """
    student = db.get(Student, student_id)
    if not student:
        raise ApiError(CODE_NOT_FOUND, "学生不存在")

    grade = student.grade or 4
    # 难度映射：L1→1, L2→3, L3→5（沿用既有 difficulty 枚举 1/3/5）
    mapped_difficulty = {1: 1, 2: 3, 3: 5}.get(difficulty, 1)

    stmt = (
        select(Question)
        .where(Question.grade == grade)
        .where(Question.difficulty == mapped_difficulty)
        .where(Question.status == "active")
        .where(Question.ship_ready == True)
    )
    if scenario_type:
        stmt = stmt.where(Question.scenario_type == scenario_type)

    rows = db.execute(stmt).scalars().all()
    pool: List[Question] = list(rows)
    if len(pool) < count:
        # 降级：放宽 difficulty 再试一次
        stmt2 = (
            select(Question)
            .where(Question.grade == grade)
            .where(Question.status == "active")
            .where(Question.ship_ready == True)
        )
        if scenario_type:
            stmt2 = stmt2.where(Question.scenario_type == scenario_type)
        pool = list(db.execute(stmt2).scalars().all())

    if len(pool) < count:
        raise ApiError(CODE_BAD_REQUEST, f"题库不足：需要 {count} 题，实际 {len(pool)} 题")

    return random.sample(pool, count)


# ------------------------------------------------------------------ 限时挑战

def start_speed_challenge(
    db: Session,
    student_id: uuid.UUID,
    difficulty: Optional[int] = None,
    scenario_type: Optional[str] = None,
) -> Tuple[GameSession, List[Dict[str, Any]]]:
    """限时挑战开局：建 session、抽题、返回题目列表（不含答案）。

    Returns:
        (game_session, questions_list)
    """
    diff = difficulty or get_recommended_difficulty(db, student_id)
    questions = _draw_questions_for_game(
        db, student_id, diff, SPEED_QUESTION_COUNT, scenario_type
    )

    session = GameSession(
        id=uuid.uuid4(),
        student_id=student_id,
        game_type="speed_challenge",
        score=0,
        correct_count=0,
        wrong_count=0,
        max_combo=0,
        duration_sec=0,
        difficulty_level=diff,
        battle_wave=0,
        battle_best_wave=0,
        session_metadata={
            "question_ids": [str(q.id) for q in questions],
            "answers": [],
            "start_time": datetime.now(timezone.utc).isoformat(),
        },
        created_at=utcnow(),
        ended_at=None,
    )
    db.add(session)
    db.flush()

    # 返回脱敏题目列表
    q_list = [
        {
            "id": str(q.id),
            "content": q.content,
            "image_url": q.image_url,
            "type": q.type_,
            "options": q.options,
            "scenario_type": q.scenario_type,
        }
        for q in questions
    ]
    return session, q_list


def submit_speed_answer(
    db: Session,
    game_session_id: uuid.UUID,
    question_id: uuid.UUID,
    student_answer: str,
    elapsed_ms: int,
) -> Dict[str, Any]:
    """限时挑战逐题判分。

    直接更新 ``game_sessions.session_metadata`` 中的答题记录，
    不单独建 ``Attempt``（避免污染学习域的 daily 统计口径）。

    Returns:
        {"is_correct": bool, "combo": int, "score_gain": int}
    """
    session = db.get(GameSession, game_session_id)
    if not session or session.game_type != "speed_challenge":
        raise ApiError(CODE_NOT_FOUND, "游戏会话不存在或类型不符")

    question = db.get(Question, question_id)
    if not question:
        raise ApiError(CODE_NOT_FOUND, "题目不存在")

    std_answer = question.answer.correct_answer if question.answer else ""
    outcome = judge_engine.judge(
        q_type=question.type_,
        options=question.options,
        correct_answer=std_answer,
        selected_answer=student_answer,
    )
    is_correct = outcome.is_correct

    meta = dict(session.session_metadata or {})
    answers: List[Dict[str, Any]] = list(meta.get("answers", []))
    combo = meta.get("current_combo", 0)
    score = meta.get("current_score", 0)

    if is_correct:
        combo += 1
        gain = SPEED_BASE_SCORE + combo * SPEED_COMBO_BONUS
        elapsed_sec = elapsed_ms / 1000.0
        if elapsed_sec <= SPEED_FAST_THRESHOLD:
            gain += SPEED_FAST_BONUS
        score += gain
        result = {"is_correct": True, "combo": combo, "score_gain": gain}
    else:
        combo = 0
        result = {"is_correct": False, "combo": 0, "score_gain": 0}

    answers.append(
        {
            "question_id": str(question_id),
            "student_answer": student_answer,
            "is_correct": is_correct,
            "elapsed_ms": elapsed_ms,
            "score_gain": result["score_gain"],
        }
    )
    meta["answers"] = answers
    meta["current_combo"] = combo
    meta["current_score"] = score
    meta["max_combo"] = max(meta.get("max_combo", 0), combo)
    session.session_metadata = meta
    db.flush()
    return result


def finish_speed_challenge(
    db: Session,
    game_session_id: uuid.UUID,
) -> GameResult:
    """限时挑战结算。

    步骤：
      1. 从 metadata 汇总成绩；
      2. 更新 game_sessions 终态；
      3. 答错题调用 ``record_mistake`` 入库；
      4. 更新 student_points；
      5. 徽章判定；
      6. 返回结算结果。
    """
    session = db.get(GameSession, game_session_id)
    if not session or session.game_type != "speed_challenge":
        raise ApiError(CODE_NOT_FOUND, "游戏会话不存在")
    if session.ended_at is not None:
        raise ApiError(CODE_BAD_REQUEST, "本局已结算")

    meta = dict(session.session_metadata or {})
    answers: List[Dict[str, Any]] = list(meta.get("answers", []))
    score = meta.get("current_score", 0)
    max_combo = meta.get("max_combo", 0)
    correct = sum(1 for a in answers if a.get("is_correct"))
    wrong = len(answers) - correct

    start_str = meta.get("start_time", "")
    start_dt = datetime.fromisoformat(start_str) if start_str else session.created_at
    duration_sec = int((datetime.now(timezone.utc) - start_dt).total_seconds())

    # 1. 更新 session 终态
    session.score = score
    session.correct_count = correct
    session.wrong_count = wrong
    session.max_combo = max_combo
    session.duration_sec = duration_sec
    session.ended_at = utcnow()
    db.flush()

    # 2. 错题入本（C4）
    for ans in answers:
        if not ans.get("is_correct"):
            qid = uuid.UUID(ans["question_id"])
            question = db.get(Question, qid)
            if question:
                learning_service.record_mistake(
                    db=db,
                    student_id=session.student_id,
                    question_id=qid,
                    error_type="game_wrong",  # 游戏域专用错因标签
                )

    # 3. 更新积分表
    point_summary = _update_student_points(
        db,
        session.student_id,
        game_type="speed_challenge",
        score=score,
        correct=correct,
        wrong=wrong,
        best_wave=0,
    )

    # 4. 徽章判定
    new_badges = _check_and_award_badges(
        db,
        session.student_id,
        game_type="speed_challenge",
        score=score,
        correct=correct,
        wrong=wrong,
        max_combo=max_combo,
        duration_sec=duration_sec,
        total_games=point_summary["total_games"],
    )

    return GameResult(
        score=score,
        correct_count=correct,
        wrong_count=wrong,
        max_combo=max_combo,
        duration_sec=duration_sec,
        new_badges=new_badges,
        point_summary=point_summary,
    )


# ------------------------------------------------------------------ 回合战斗

def start_math_battle(
    db: Session,
    student_id: uuid.UUID,
    difficulty: Optional[int] = None,
) -> Tuple[GameSession, Dict[str, Any]]:
    """回合战斗开局：建 session、抽 10 波题目、返回首波怪物信息。

    Returns:
        (game_session, {"player_hp": int, "monster": dict, "question": dict})
    """
    diff = difficulty or get_recommended_difficulty(db, student_id)
    questions = _draw_questions_for_game(db, student_id, diff, BATTLE_TOTAL_WAVES)

    session = GameSession(
        id=uuid.uuid4(),
        student_id=student_id,
        game_type="math_battle",
        score=0,
        correct_count=0,
        wrong_count=0,
        max_combo=0,
        duration_sec=0,
        difficulty_level=diff,
        battle_wave=1,
        battle_best_wave=0,
        session_metadata={
            "question_ids": [str(q.id) for q in questions],
            "answers": [],
            "start_time": datetime.now(timezone.utc).isoformat(),
            "player_hp": BATTLE_PLAYER_HP,
        },
        created_at=utcnow(),
        ended_at=None,
    )
    db.add(session)
    db.flush()

    monster = _build_monster(1, diff)
    q0 = questions[0]
    q_dict = {
        "id": str(q0.id),
        "content": q0.content,
        "image_url": q0.image_url,
        "type": q0.type_,
        "options": q0.options,
    }
    return session, {
        "player_hp": BATTLE_PLAYER_HP,
        "monster": monster,
        "question": q_dict,
    }


def submit_battle_answer(
    db: Session,
    game_session_id: uuid.UUID,
    question_id: uuid.UUID,
    student_answer: str,
) -> Dict[str, Any]:
    """回合战斗单题判分。

    Returns:
        {
            "is_correct": bool,
            "player_hp": int,
            "monster_hp": int,
            "wave": int,
            "game_over": bool,
            "next_monster": Optional[dict],
            "next_question": Optional[dict],
        }
    """
    session = db.get(GameSession, game_session_id)
    if not session or session.game_type != "math_battle":
        raise ApiError(CODE_NOT_FOUND, "游戏会话不存在或类型不符")

    question = db.get(Question, question_id)
    if not question:
        raise ApiError(CODE_NOT_FOUND, "题目不存在")

    std_answer = question.answer.correct_answer if question.answer else ""
    outcome = judge_engine.judge(
        q_type=question.type_,
        options=question.options,
        correct_answer=std_answer,
        selected_answer=student_answer,
    )
    is_correct = outcome.is_correct

    meta = dict(session.session_metadata or {})
    answers: List[Dict[str, Any]] = list(meta.get("answers", []))
    wave = session.battle_wave
    diff = session.difficulty_level
    player_hp = meta.get("player_hp", BATTLE_PLAYER_HP)

    monster = _build_monster(wave, diff)
    if is_correct:
        # 玩家攻击：怪物扣血（这里简化为直接击败）
        monster_hp = 0
        correct_count = session.correct_count + 1
        wrong_count = session.wrong_count
    else:
        # 怪物反击：玩家扣血
        monster_hp = monster["hp"]  # 保持满血
        player_hp -= monster["atk"]
        correct_count = session.correct_count
        wrong_count = session.wrong_count + 1

    answers.append(
        {
            "question_id": str(question_id),
            "student_answer": student_answer,
            "is_correct": is_correct,
            "wave": wave,
            "player_hp_after": player_hp,
        }
    )
    meta["answers"] = answers
    meta["player_hp"] = player_hp
    session.session_metadata = meta
    session.correct_count = correct_count
    session.wrong_count = wrong_count
    db.flush()

    game_over = player_hp <= 0 or wave >= BATTLE_TOTAL_WAVES
    if not game_over and is_correct:
        # 进入下一波
        wave += 1
        session.battle_wave = wave
        next_q = _get_question_by_index(db, meta.get("question_ids", []), wave - 1)
        next_monster = _build_monster(wave, diff)
        next_q_dict = {
            "id": str(next_q.id),
            "content": next_q.content,
            "image_url": next_q.image_url,
            "type": next_q.type_,
            "options": next_q.options,
        } if next_q else None
        return {
            "is_correct": True,
            "player_hp": player_hp,
            "monster_hp": 0,
            "wave": wave - 1,
            "game_over": False,
            "next_monster": next_monster,
            "next_question": next_q_dict,
        }

    if game_over:
        session.ended_at = utcnow()
        db.flush()

    return {
        "is_correct": is_correct,
        "player_hp": max(player_hp, 0),
        "monster_hp": monster_hp,
        "wave": wave,
        "game_over": game_over,
        "next_monster": None,
        "next_question": None,
    }


def finish_math_battle(
    db: Session,
    game_session_id: uuid.UUID,
) -> GameResult:
    """回合战斗结算。"""
    session = db.get(GameSession, game_session_id)
    if not session or session.game_type != "math_battle":
        raise ApiError(CODE_NOT_FOUND, "游戏会话不存在")
    if session.ended_at is not None:
        raise ApiError(CODE_BAD_REQUEST, "本局已结算")

    meta = dict(session.session_metadata or {})
    answers: List[Dict[str, Any]] = list(meta.get("answers", []))
    wave = session.battle_wave
    correct = session.correct_count
    wrong = session.wrong_count

    # 得分 = 波次分 + 稀有加成
    score = wave * BATTLE_WAVE_BASE_SCORE
    if wave >= 5:
        score += BATTLE_ELITE_BONUS
    if wave >= 10:
        score += BATTLE_BOSS_BONUS

    start_str = meta.get("start_time", "")
    start_dt = datetime.fromisoformat(start_str) if start_str else session.created_at
    duration_sec = int((datetime.now(timezone.utc) - start_dt).total_seconds())

    session.score = score
    session.duration_sec = duration_sec
    session.battle_best_wave = wave
    session.ended_at = utcnow()
    db.flush()

    # 错题入本
    for ans in answers:
        if not ans.get("is_correct"):
            qid = uuid.UUID(ans["question_id"])
            question = db.get(Question, qid)
            if question:
                learning_service.record_mistake(
                    db=db,
                    student_id=session.student_id,
                    question_id=qid,
                    error_type="game_wrong",
                )

    # 更新积分
    point_summary = _update_student_points(
        db,
        session.student_id,
        game_type="math_battle",
        score=score,
        correct=correct,
        wrong=wrong,
        best_wave=wave,
    )

    # 徽章判定
    new_badges = _check_and_award_badges(
        db,
        session.student_id,
        game_type="math_battle",
        score=score,
        correct=correct,
        wrong=wrong,
        max_combo=0,
        duration_sec=duration_sec,
        total_games=point_summary["total_games"],
        best_wave=wave,
    )

    return GameResult(
        score=score,
        correct_count=correct,
        wrong_count=wrong,
        max_combo=0,
        duration_sec=duration_sec,
        new_badges=new_badges,
        point_summary=point_summary,
    )


# ------------------------------------------------------------------ 怪物构建辅助

def _build_monster(wave: int, difficulty: int) -> Dict[str, Any]:
    """构建第 wave 波的怪物信息。"""
    base_atk = BATTLE_MONSTER_ATK_BASE + wave * BATTLE_MONSTER_ATK_PER_WAVE
    if wave == 5:
        base_atk += BATTLE_ELITE_ATK_BONUS
        name = "石头巨人（精英）"
        rarity = "elite"
    elif wave == 10:
        base_atk += BATTLE_BOSS_ATK_BONUS
        name = "暗影魔王（Boss）"
        rarity = "boss"
    elif wave in (3, 7, 9):
        name = f"第{wave}波怪物"
        rarity = "normal"
    else:
        name = f"第{wave}波怪物"
        rarity = "normal"

    # HP 反推：普通怪 2-6 题、精英 5-8 题、Boss 9 题击杀
    player_atk = BATTLE_PLAYER_ATK + BATTLE_DIFF_ATK_BONUS.get(difficulty, 0)
    if rarity == "elite":
        hp = player_atk * random.randint(5, 8)
    elif rarity == "boss":
        hp = player_atk * 9
    else:
        hp = player_atk * random.randint(2, 6)

    return {
        "wave": wave,
        "name": name,
        "rarity": rarity,
        "hp": hp,
        "atk": base_atk,
        "player_atk": player_atk,
    }


def _get_question_by_index(
    db: Session, question_ids: List[str], index: int
) -> Optional[Question]:
    if index < 0 or index >= len(question_ids):
        return None
    return db.get(Question, uuid.UUID(question_ids[index]))


# ------------------------------------------------------------------ 积分与排行榜

def _update_student_points(
    db: Session,
    student_id: uuid.UUID,
    game_type: str,
    score: int,
    correct: int,
    wrong: int,
    best_wave: int,
) -> Dict[str, int]:
    """原子更新学生积分总览，返回更新后的汇总值。"""
    point = db.execute(
        select(StudentPoint).where(StudentPoint.student_id == student_id)
    ).scalar_one_or_none()

    if not point:
        point = StudentPoint(
            id=uuid.uuid4(),
            student_id=student_id,
            total_points=score,
            weekly_points=score,
            monthly_points=score,
            total_games=1,
            total_correct=correct,
            total_wrong=wrong,
            speed_best_score=score if game_type == "speed_challenge" else 0,
            battle_best_wave=best_wave if game_type == "math_battle" else 0,
            last_played_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(point)
    else:
        point.total_points += score
        point.weekly_points += score
        point.monthly_points += score
        point.total_games += 1
        point.total_correct += correct
        point.total_wrong += wrong
        if game_type == "speed_challenge" and score > point.speed_best_score:
            point.speed_best_score = score
        if game_type == "math_battle" and best_wave > point.battle_best_wave:
            point.battle_best_wave = best_wave
        point.last_played_at = utcnow()
        point.updated_at = utcnow()

    db.flush()
    return {
        "total_points": point.total_points,
        "weekly_points": point.weekly_points,
        "monthly_points": point.monthly_points,
        "total_games": point.total_games,
        "speed_best_score": point.speed_best_score,
        "battle_best_wave": point.battle_best_wave,
    }


def refresh_leaderboard(
    db: Session,
    rank_type: str,  # "weekly" | "monthly"
) -> None:
    """刷新指定类型的排行榜快照（供定时任务调用）。"""
    now = datetime.now(timezone.utc)
    if rank_type == "weekly":
        # ISO 周标识：2026W38
        period = f"{now.year}{RANK_WEEK_PREFIX}{now.isocalendar()[1]:02d}"
        points_col = "weekly_points"
    elif rank_type == "monthly":
        period = f"{now.year}-{now.month:02d}"
        points_col = "monthly_points"
    else:
        raise ApiError(CODE_BAD_REQUEST, "rank_type 必须是 weekly 或 monthly")

    # 清旧榜
    db.execute(
        __import__("sqlalchemy").delete(Leaderboard).where(
            Leaderboard.rank_type == rank_type,
            Leaderboard.rank_period == period,
        )
    )

    # 取当前周期积分 Top 100
    stmt = (
        select(StudentPoint)
        .where(getattr(StudentPoint, points_col) > 0)
        .order_by(getattr(StudentPoint, points_col).desc())
        .limit(100)
    )
    rows = db.execute(stmt).scalars().all()

    for pos, sp in enumerate(rows, start=1):
        entry = Leaderboard(
            id=uuid.uuid4(),
            student_id=sp.student_id,
            rank_type=rank_type,
            rank_period=period,
            rank_position=pos,
            score=getattr(sp, points_col),
            games_count=sp.total_games,
            best_wave=sp.battle_best_wave,
            updated_at=utcnow(),
        )
        db.add(entry)
    db.flush()


# ------------------------------------------------------------------ 徽章系统

def award_badge(
    db: Session,
    student_id: uuid.UUID,
    badge_id: uuid.UUID,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[StudentBadge]:
    """发放徽章（幂等：已存在则返回 None）。"""
    existing = db.execute(
        select(StudentBadge).where(
            StudentBadge.student_id == student_id,
            StudentBadge.badge_id == badge_id,
        )
    ).scalar_one_or_none()
    if existing:
        return None

    sb = StudentBadge(
        id=uuid.uuid4(),
        student_id=student_id,
        badge_id=badge_id,
        unlocked_at=utcnow(),
        unlock_metadata=metadata or {},
    )
    db.add(sb)
    db.flush()
    return sb


def _check_and_award_badges(
    db: Session,
    student_id: uuid.UUID,
    game_type: str,
    score: int,
    correct: int,
    wrong: int,
    max_combo: int,
    duration_sec: int,
    total_games: int,
    best_wave: int = 0,
) -> List[Badge]:
    """根据游戏结果判定应发徽章，逐条检查条件并发放。"""
    all_badges = db.execute(select(Badge).order_by(Badge.sort_order)).scalars().all()
    new_badges: List[Badge] = []

    # 预计算常用指标
    total = correct + wrong
    accuracy = correct / total if total > 0 else 0.0
    avg_time = duration_sec / total if total > 0 else 999.0

    for badge in all_badges:
        cond_type = badge.condition_type
        cond_val = badge.condition_value or 0
        awarded = False

        if cond_type == "max_streak" and max_combo >= cond_val:
            awarded = True
        elif cond_type == "accuracy_perfect" and accuracy >= 1.0:
            awarded = True
        elif cond_type == "accuracy_total" and correct >= cond_val:
            awarded = True
        elif cond_type == "avg_time_sec" and avg_time <= cond_val:
            awarded = True
        elif cond_type == "first_game" and total_games >= cond_val:
            awarded = True
        elif cond_type == "total_games" and total_games >= cond_val:
            awarded = True
        elif cond_type == "first_scenario":
            # 由上层或 API 层在首次完成场景题时显式调用 award_badge
            pass
        elif cond_type == "battle_wave" and best_wave >= cond_val:
            awarded = True

        if awarded:
            sb = award_badge(
                db,
                student_id,
                badge.id,
                metadata={
                    "game_type": game_type,
                    "score": score,
                    "max_combo": max_combo,
                    "best_wave": best_wave,
                },
            )
            if sb:
                new_badges.append(badge)

    return new_badges


# ------------------------------------------------------------------ 查询接口（供 API 层）

def get_student_badges(db: Session, student_id: uuid.UUID) -> List[Dict[str, Any]]:
    """获取学生已获得的徽章列表（含徽章详情）。"""
    rows = db.execute(
        select(StudentBadge, Badge)
        .join(Badge, StudentBadge.badge_id == Badge.id)
        .where(StudentBadge.student_id == student_id)
        .order_by(StudentBadge.unlocked_at.desc())
    ).all()

    return [
        {
            "badge_id": str(b.id),
            "name": b.name,
            "display_name": b.display_name,
            "description": b.description,
            "category": b.category,
            "rarity": b.rarity,
            "icon_url": b.icon_url,
            "unlocked_at": sb.unlocked_at.isoformat() if sb.unlocked_at else None,
            "unlock_metadata": sb.unlock_metadata,
        }
        for sb, b in rows
    ]


def get_leaderboard(
    db: Session,
    rank_type: str,
    period: Optional[str] = None,
    top_n: int = 50,
) -> List[Dict[str, Any]]:
    """获取排行榜（默认取当前周期）。"""
    now = datetime.now(timezone.utc)
    if period is None:
        if rank_type == "weekly":
            period = f"{now.year}{RANK_WEEK_PREFIX}{now.isocalendar()[1]:02d}"
        else:
            period = f"{now.year}-{now.month:02d}"

    rows = db.execute(
        select(Leaderboard, Student)
        .join(Student, Leaderboard.student_id == Student.id)
        .where(Leaderboard.rank_type == rank_type)
        .where(Leaderboard.rank_period == period)
        .order_by(Leaderboard.rank_position.asc())
        .limit(top_n)
    ).all()

    return [
        {
            "rank": lb.rank_position,
            "student_id": str(lb.student_id),
            "student_name": st.real_name or "匿名同学",
            "score": lb.score,
            "games_count": lb.games_count,
            "best_wave": lb.best_wave,
        }
        for lb, st in rows
    ]


def get_game_history(
    db: Session,
    student_id: uuid.UUID,
    game_type: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """获取学生最近的游戏记录。"""
    stmt = (
        select(GameSession)
        .where(GameSession.student_id == student_id)
        .where(GameSession.ended_at.isnot(None))
        .order_by(GameSession.ended_at.desc())
        .limit(limit)
    )
    if game_type:
        stmt = stmt.where(GameSession.game_type == game_type)

    rows = db.execute(stmt).scalars().all()
    return [
        {
            "id": str(s.id),
            "game_type": s.game_type,
            "score": s.score,
            "correct_count": s.correct_count,
            "wrong_count": s.wrong_count,
            "max_combo": s.max_combo,
            "duration_sec": s.duration_sec,
            "difficulty_level": s.difficulty_level,
            "battle_best_wave": s.battle_best_wave,
            "ended_at": s.ended_at.isoformat() if s.ended_at else None,
        }
        for s in rows
    ]
