"""游戏化学习接口 Schema（方向六 · B7）。

硬约束：
    * 全部 snake_case（C10）；
    * 可选字段用 Optional + default=None，避免前端不传报错。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ------------------------------------------------------------------ 游戏大厅

class HubResponse(BaseModel):
    total_points: int = 0
    weekly_points: int = 0
    monthly_points: int = 0
    speed_best_score: int = 0
    battle_best_wave: int = 0
    total_games: int = 0
    badge_count: int = 0
    badge_total: int = 15
    recent_games: List[Dict[str, Any]] = Field(default_factory=list)


# ------------------------------------------------------------------ 限时挑战

class SpeedStartRequest(BaseModel):
    difficulty: Optional[int] = Field(default=None, ge=1, le=3)
    scenario_type: Optional[str] = None


class SpeedStartResponse(BaseModel):
    game_session_id: str
    duration_sec: int = 60
    questions: List[Dict[str, Any]]


class SpeedAnswerRequest(BaseModel):
    game_session_id: str
    question_id: str
    student_answer: str
    elapsed_ms: int = 0


class SpeedAnswerResponse(BaseModel):
    is_correct: bool
    combo: int
    score_gain: int


class SpeedFinishRequest(BaseModel):
    game_session_id: str


class SpeedFinishResponse(BaseModel):
    score: int
    correct_count: int
    wrong_count: int
    max_combo: int
    duration_sec: int
    new_badges: List[Dict[str, Any]] = Field(default_factory=list)
    point_summary: Dict[str, int] = Field(default_factory=dict)


# ------------------------------------------------------------------ 回合战斗

class BattleStartRequest(BaseModel):
    difficulty: Optional[int] = Field(default=None, ge=1, le=3)


class BattleStartResponse(BaseModel):
    game_session_id: str
    player_hp: int
    monster: Dict[str, Any]
    question: Dict[str, Any]


class BattleAnswerRequest(BaseModel):
    game_session_id: str
    question_id: str
    student_answer: str


class BattleAnswerResponse(BaseModel):
    is_correct: bool
    player_hp: int
    monster_hp: int
    wave: int
    game_over: bool
    next_monster: Optional[Dict[str, Any]] = None
    next_question: Optional[Dict[str, Any]] = None


class BattleFinishRequest(BaseModel):
    game_session_id: str


class BattleFinishResponse(BaseModel):
    score: int
    correct_count: int
    wrong_count: int
    best_wave: int
    duration_sec: int
    new_badges: List[Dict[str, Any]] = Field(default_factory=list)
    point_summary: Dict[str, int] = Field(default_factory=dict)


# ------------------------------------------------------------------ 徽章 / 排行榜 / 历史

class BadgeItem(BaseModel):
    badge_id: str
    name: str
    display_name: str
    description: str
    category: str
    rarity: str
    icon_url: Optional[str] = None
    unlocked_at: Optional[str] = None
    unlock_metadata: Optional[Dict[str, Any]] = None


class LeaderboardItem(BaseModel):
    rank: int
    student_id: str
    student_name: str
    score: int
    games_count: int
    best_wave: int


class GameHistoryItem(BaseModel):
    id: str
    game_type: str
    score: int
    correct_count: int
    wrong_count: int
    max_combo: int
    duration_sec: int
    difficulty_level: int
    battle_best_wave: int
    ended_at: Optional[str] = None
