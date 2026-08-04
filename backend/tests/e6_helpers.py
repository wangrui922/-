"""Epic6 家长周报测试共用工具。

复用 T04 的造数据能力（``t04_helpers``），补充：
    * ``ReportFakeArk`` + ``patch_report_ark``：把 Report Agent 的方舟调用换成
      返回合法 JSON 的假实现，便于验证 ``source == "ai"`` 分支；
    * ``seed_report_week``：一站式造「一个知识点 + 一道题 + 若干作答 + 一条错题
      + 一次辅导会话」，覆盖学习投入统计的全部计数维度。

本模块不以 ``test_`` 开头，pytest 不会收集它。
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

from app.db import SessionLocal
from app.models.ai import AiSession
from app.models.base import utcnow

# 直接复用 T04 的造数能力
from t04_helpers import (  # noqa: F401
    login,
    make_kp,
    make_question,
    seed_attempts,
    seed_kp_and_questions,
    seed_mistake,
    token_headers,
)


class ReportFakeArk:
    """``ark_respond`` 的假实现：返回符合 §4.3 契约的 JSON。"""

    def __init__(
        self,
        *,
        headline: str = "本周练了 4 道题，计算这块还要多练",
        overview: str = "这周学了 3 天，一共做了 4 道题，做对 3 道，正确率 75%。",
        progress: str = "和上周比，正确率提高了 10 个百分点，有进步。",
    ) -> None:
        self.headline = headline
        self.overview = overview
        self.progress = progress
        self.calls: List[Dict[str, Any]] = []

    def __call__(
        self,
        *,
        messages: List[Dict[str, Any]],
        system: Optional[str] = None,
        image_b64: Optional[str] = None,
        timeout: float = 60.0,
    ) -> str:
        self.calls.append({"messages": messages, "system": system})
        payload = {
            "headline": self.headline,
            "overview_text": self.overview,
            "progress_text": self.progress,
            "advice_items": [
                "每天 10 分钟两位数乘法口算，一次 10 道，连做 5 天",
                "把本周做错的题挑 3 道重做一次",
            ],
        }
        return json.dumps(payload, ensure_ascii=False)


def patch_report_ark(monkeypatch, fake: Optional[ReportFakeArk] = None) -> ReportFakeArk:
    """把 Report Agent 用到的 ``ark_respond`` 换成假实现（测试结束自动还原）。"""
    ark = fake or ReportFakeArk()
    monkeypatch.setattr("app.ai.agents.report.ark_respond", ark)
    return ark


def seed_report_week(
    student_id: uuid.UUID,
    *,
    correct: int = 3,
    wrong: int = 1,
    solved: int = 0,
    days_ago: float = 0.0,
) -> Dict[str, Any]:
    """造一份「本周有练习」的迷你场景，覆盖学习投入的全部计数。

    Args:
        student_id: 学生 ID。
        correct: 正确作答次数。
        wrong: 错误作答次数。
        solved: 其中已攻克的错题数（<= wrong）。
        days_ago: 距今天数（默认 0，落在当前自然周）。

    Returns:
        ``{"kp_id","question_id","mistake_id"}``。
    """
    kp_id, question_ids = seed_kp_and_questions("四则运算", question_count=1)
    question_id = question_ids[0]
    results = [True] * max(0, correct) + [False] * max(0, wrong)
    if results:
        seed_attempts(student_id, question_id, results, days_ago=days_ago)
    mistake_id = seed_mistake(student_id, question_id, days_ago=days_ago, solved=bool(solved))

    # 一次辅导会话
    with SessionLocal() as db:
        db.add(
            AiSession(
                student_id=student_id,
                status="active",
                created_at=utcnow() - timedelta(days=days_ago),
            )
        )
        db.commit()

    return {"kp_id": kp_id, "question_id": question_id, "mistake_id": mistake_id}


__all__ = [
    "ReportFakeArk",
    "patch_report_ark",
    "seed_report_week",
    "login",
    "token_headers",
    "make_kp",
    "make_question",
    "seed_attempts",
    "seed_kp_and_questions",
    "seed_mistake",
]
