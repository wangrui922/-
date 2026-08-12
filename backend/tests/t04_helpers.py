"""T04 学习画像测试共用工具：登录 + 造数据（知识点/题目/作答/错题/会话）。

与 ``t03_helpers`` 的分工：
    * ``t03_helpers`` 负责**方舟插桩**与**统一响应断言**（``assert_ok`` / ``assert_fail``），
      T04 直接 re-export 复用，不重复造轮子；
    * 本模块只负责 **T04 的造数据**：知识点树、题目、作答流水、错题、AI 会话消息。

设计要点：
    * 所有写库都开独立 ``SessionLocal``，写完即 commit，避免与接口内部事务打架；
    * 支持指定 ``days_ago``，方便验证**时效衰减**（14 天半衰期）与
      ``range_days`` 窗口过滤；
    * 每个 helper 都返回字符串 ID，测试里直接和接口返回值比对。

本模块不以 ``test_`` 开头，pytest 不会收集它。
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.db import SessionLocal
from app.models.ai import AiMessage, AiSession
from app.models.base import utcnow
from app.models.content import KnowledgePoint, Question
from app.models.learning import Attempt, Mistake
from app.models.student_mastery import StudentMastery

# 直接复用 T03 的断言与插桩，保持全项目一套口径
from t03_helpers import assert_fail, assert_ok, patch_ark  # noqa: F401

#: 验证码已改为随机生成，DEV_CODE 保留仅为向后兼容引用
DEV_CODE = ""


# ==================================================================== 登录
def login(client, phone: str = "13800000000") -> Tuple[Dict[str, str], uuid.UUID]:
    """随机验证码登录，返回 ``(请求头, 学生 uuid)``。

    流程：发码 → 取 devCode → 登录。
    需在 conftest ``_reset_rate_limiters`` autouse fixture 作用下使用。

    Args:
        client: ``TestClient``。
        phone: 手机号；不同手机号 = 不同学生，用于跨用户越权用例。

    Returns:
        ``({"Authorization": "Bearer ..."}, student_id)``。
    """
    # 发码
    resp = client.post("/api/auth/code", json={"phone": phone})
    assert resp.status_code == 200, f"发码失败: {resp.text}"
    code = resp.json()["data"].get("devCode")
    assert code, f"dev 环境应回带验证码: {resp.text}"

    # 登录
    resp = client.post("/api/auth/login", json={"phone": phone, "code": code})
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    data = resp.json()["data"]
    student_id = data["user"]["studentId"]
    assert student_id, f"登录应自动建学生档案: {data}"
    return {"Authorization": f"Bearer {data['access']}"}, uuid.UUID(str(student_id))


def token_headers(client, phone: str = "13800000000") -> Dict[str, str]:
    """只要请求头，不关心学生 ID 时的简写。"""
    return login(client, phone)[0]


# ==================================================================== 造数据
def make_kp(name: str, *, sort: int = 0) -> str:
    """插一个知识点，返回其 ID（字符串）。

    Args:
        name: 知识点名称。
        sort: 排序权重。

    Returns:
        知识点 ID 字符串。
    """
    with SessionLocal() as db:
        kp = KnowledgePoint(name=name, sort=sort)
        db.add(kp)
        db.commit()
        return str(kp.id)


def make_question(
    *,
    kp_id: Optional[str] = None,
    content: str = "T04 测试题：3x - 5 = 7，求 x",
    status: str = "active",
) -> str:
    """插一道题，返回其 ID（字符串）。

    Args:
        kp_id: 关联知识点 ID；``None`` 表示不关联（用于验证 R5 未映射错题）。
        content: 题干。
        status: ``active`` / ``archived``，推荐接口只挑 active。

    Returns:
        题目 ID 字符串。
    """
    with SessionLocal() as db:
        question = Question(
            content=content,
            subject="数学",
            grade=3,
            difficulty=3,
            type_="计算",
            kp_id=uuid.UUID(kp_id) if kp_id else None,
            status=status,
        )
        db.add(question)
        db.commit()
        return str(question.id)


def seed_kp_and_questions(
    kp_name: str,
    *,
    question_count: int = 1,
    status: str = "active",
) -> Tuple[str, List[str]]:
    """一次性造「一个知识点 + N 道题」。

    Args:
        kp_name: 知识点名称。
        question_count: 题目数量。
        status: 题目状态。

    Returns:
        ``(kp_id, [question_id, ...])``。
    """
    kp_id = make_kp(kp_name)
    question_ids = [
        make_question(kp_id=kp_id, content=f"{kp_name} 第 {index + 1} 题", status=status)
        for index in range(max(0, question_count))
    ]
    return kp_id, question_ids


def seed_attempts(
    student_id: uuid.UUID,
    question_id: str,
    results: Sequence[bool],
    *,
    days_ago: float = 0.0,
) -> List[str]:
    """批量插作答流水。

    ⚠️ 时间必须**严格落在过去**：``student_mastery`` 的脏检查口径是
    ``last_practiced_at > computed_at 即视为有新练习``，若造出未来时间的作答，
    缓存会被永久判脏、``mode`` 永远回不到 ``cache``。所以这里按
    ``stamp - (N - index) 秒`` 展开：保持 index 越大越晚的顺序，且全部早于 ``stamp``。

    Args:
        student_id: 学生 ID。
        question_id: 题目 ID。
        results: 每次作答是否正确，如 ``[True, True, False]``。
        days_ago: 距今天数（用于验证时效衰减）。

    Returns:
        新建的 attempt ID 列表。
    """
    stamp = utcnow() - timedelta(days=float(days_ago))
    total = len(results)
    created: List[str] = []
    with SessionLocal() as db:
        for index, is_correct in enumerate(results):
            attempt = Attempt(
                student_id=student_id,
                question_id=uuid.UUID(question_id),
                is_correct=bool(is_correct),
                created_at=stamp - timedelta(seconds=(total - index)),
                client_event_id=f"t04-{uuid.uuid4().hex}",
            )
            db.add(attempt)
            db.flush()
            created.append(str(attempt.id))
        db.commit()
    return created


def seed_mistake(
    student_id: uuid.UUID,
    question_id: str,
    *,
    error_type: Optional[str] = "计算错误",
    error_reason: Optional[str] = "进位忘了",
    solved: bool = False,
    days_ago: float = 0.0,
) -> str:
    """插一条错题，返回其 ID（字符串）。

    Args:
        student_id: 学生 ID。
        question_id: 题目 ID。
        error_type: 错误类型（参与错因分类）。
        error_reason: 错误原因（参与错因分类）。
        solved: 是否已攻克。
        days_ago: 距今天数（用于验证 ``range_days`` 窗口）。

    Returns:
        错题 ID 字符串。
    """
    stamp = utcnow() - timedelta(days=float(days_ago))
    with SessionLocal() as db:
        mistake = Mistake(
            student_id=student_id,
            question_id=uuid.UUID(question_id),
            error_type=error_type,
            error_reason=error_reason,
            solved=bool(solved),
            solved_at=stamp if solved else None,
            created_at=stamp,
        )
        db.add(mistake)
        db.commit()
        return str(mistake.id)


def make_ai_messages(
    student_id: uuid.UUID,
    question_id: str,
    contents: Sequence[str],
    *,
    role: str = "user",
) -> str:
    """造一个绑定题目的 AI 会话 + 若干条消息，返回会话 ID。

    用于验证 R3 兜底：错题没写错因时，从该题会话里取学生最后一句当证据。

    Args:
        student_id: 学生 ID。
        question_id: 题目 ID。
        contents: 消息内容列表（按顺序，时间递增）。
        role: 消息角色，默认 ``user``。

    Returns:
        会话 ID 字符串。
    """
    base = utcnow()
    total = len(contents)
    with SessionLocal() as db:
        session = AiSession(
            student_id=student_id,
            question_id=uuid.UUID(question_id),
            status="active",
            created_at=base - timedelta(seconds=total + 1),
        )
        db.add(session)
        db.flush()
        for index, content in enumerate(contents):
            # 同 seed_attempts：全部落在过去，且保持 index 越大越晚
            db.add(
                AiMessage(
                    session_id=session.id,
                    role=role,
                    content=content,
                    created_at=base - timedelta(seconds=(total - index)),
                )
            )
        db.commit()
        return str(session.id)


def seed_student_with_mistakes(
    student_id: uuid.UUID,
    *,
    kp_name: str = "一元一次方程",
    wrong: int = 2,
    right: int = 1,
    error_type: str = "计算错误",
    error_reason: str = "移项忘了变号",
) -> Dict[str, Any]:
    """一站式造「一个知识点 + 一道题 + 若干对错作答 + 一条错题」。

    Args:
        student_id: 学生 ID。
        kp_name: 知识点名称。
        wrong: 错误作答次数。
        right: 正确作答次数。
        error_type: 错题的错误类型。
        error_reason: 错题的错误原因。

    Returns:
        ``{"kp_id","question_id","mistake_id"}``。
    """
    kp_id, question_ids = seed_kp_and_questions(kp_name, question_count=1)
    question_id = question_ids[0]
    results = [False] * max(0, wrong) + [True] * max(0, right)
    if results:
        seed_attempts(student_id, question_id, results)
    mistake_id = seed_mistake(
        student_id,
        question_id,
        error_type=error_type,
        error_reason=error_reason,
    )
    return {"kp_id": kp_id, "question_id": question_id, "mistake_id": mistake_id}


# ==================================================================== 缓存查询
def load_mastery_rows(student_id: uuid.UUID) -> List[StudentMastery]:
    """直接读 ``student_mastery`` 物化表（验证缓存是否落库）。"""
    from sqlalchemy import select

    with SessionLocal() as db:
        return list(
            db.execute(
                select(StudentMastery).where(StudentMastery.student_id == student_id)
            ).scalars()
        )


def clear_mastery_cache(student_id: uuid.UUID) -> int:
    """清掉该学生的物化缓存行，返回删除条数（跨方言：逐行 delete）。"""
    from sqlalchemy import select

    with SessionLocal() as db:
        rows = list(
            db.execute(
                select(StudentMastery).where(StudentMastery.student_id == student_id)
            ).scalars()
        )
        for row in rows:
            db.delete(row)
        db.commit()
        return len(rows)


def find_pattern(patterns: Sequence[Dict[str, Any]], category: str) -> Dict[str, Any]:
    """在错因分布里按 category 取出那一项（找不到直接断言失败）。"""
    for item in patterns:
        if item.get("category") == category:
            return item
    raise AssertionError(f"错因分布里没有 {category}: {patterns}")


__all__ = [
    "DEV_CODE",
    "assert_ok",
    "assert_fail",
    "patch_ark",
    "login",
    "token_headers",
    "make_kp",
    "make_question",
    "seed_kp_and_questions",
    "seed_attempts",
    "seed_mistake",
    "make_ai_messages",
    "seed_student_with_mistakes",
    "load_mastery_rows",
    "clear_mastery_cache",
    "find_pattern",
]
