"""T6 · 按错因定向推题 —— 薄弱画像聚合 + 端到端回带验收测试。

被测对象（来自设计文档 practice-tutor-adaptive-design-20260811.md T6）：

    * ``app.services.ai_exercise_service.get_weak_profile`` ——
      纯本地 SQL 聚合 Top-2 弱项错因（五类机器码过滤 NULL / "练习错题" 脏值）。
    * ``POST /api/exercise/gen`` —— 经 ``orchestrator.generate_exercise`` 回带
      ``weak_tags`` / ``weak_source``（snake_case 透传），并把弱项拼进出题提示词
      ``context``。

用例覆盖（team-lead 任务书 A）：

    1. Top-2 排序：METHOD×12 / READ×9 / CALC×3 → weak_tags=[METHOD, READ]，source=profile。
    2. 脏数据过滤：error_type="练习错题"（TD-3 脏值）与 NULL 不得出现在结果里。
    3. fallback 空画像：无合规错题（零错题 / 仅脏值）→ weak_tags=[]，source=fallback。
    4. 端到端回带：FakeArk 打桩 + 内存 SQLite + 真实 API。
       - 有历史错题学生 → weak_tags 非空、weak_source=profile、含 label；
       - 无历史错题学生 → weak_source=fallback、weak_tags=[]、接口不 500。
    5. （可选但已实现）orchestrator 在 source=profile 时把弱项 label 拼进
       传给模型的 context（经 FakeArk 捕获请求体断言）。

零迁移红线：测试全程用 conftest 已设的内存 SQLite（StaticPool 共享连接），
直接 ``SessionLocal`` 落库，不触任何迁移、不依赖外网（方舟一律 FakeArk）。

字段命名：/exercise/gen 的 data 是 snake_case（weak_tags / weak_source），
本文件直接按后端实际键断言（设计 §3.3）。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

from app.core.error_categories import error_label
from app.db import SessionLocal
from app.models.content import Question
from app.models.learning import Mistake
from app.models.user import Student, User
from app.services.ai_exercise_service import get_weak_profile

from t03_helpers import FakeArk, assert_ok, patch_ark

#: 设计 §9 验收口径 P0-1：聚合只认五类机器码，其余一律丢弃。
VALID_ERROR_TYPES: Tuple[str, ...] = ("READ", "METHOD", "CALC", "KNOWLEDGE", "CHECK")


# ==================================================================== 公共工具
def _make_question(db) -> uuid.UUID:
    """造一条极简题目（只填 NOT NULL 的 content），返回其 id 供错题挂外键。"""
    q = Question(content="占位题（测试用，不入抽题池）")
    db.add(q)
    db.flush()
    return q.id


def _make_student(db) -> uuid.UUID:
    """直接落一条 User + Student（绕开 dev 登录），返回 student.id（uuid.UUID）。"""
    user = User(phone=f"seed-{uuid.uuid4().hex[:12]}", role="student")
    db.add(user)
    db.flush()
    student = Student(user_id=user.id)
    db.add(student)
    db.flush()
    return student.id


def _seed_mistakes_for(student_id: uuid.UUID, *pairs: Tuple[Optional[str], int]) -> None:
    """为给定学生批量落错题。

    Args:
        student_id: 目标学生 id。
        pairs: 若干 ``(error_type, count)``，``error_type=None`` 表示数据库 NULL。
    """
    with SessionLocal() as db:
        qid = _make_question(db)
        for etype, n in pairs:
            for _ in range(n):
                db.add(Mistake(student_id=student_id, question_id=qid, error_type=etype))
        db.commit()


def _auth(client, phone: str) -> Tuple[Dict[str, str], uuid.UUID]:
    """dev 万能码登录，返回 ``(请求头, 学生 uuid)``。"""
    resp = client.post("/api/auth/login", json={"phone": phone, "code": "000000"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    student_id = data["user"]["studentId"]
    assert student_id, f"dev 登录应自动建学生档案，studentId 不应为空: {data}"
    return {"Authorization": f"Bearer {data['access']}"}, uuid.UUID(str(student_id))


def _start_session(client, headers: Dict[str, str], text: str = "解方程：2x + 3 = 7") -> str:
    """开一个辅导会话，返回 sessionId（供出题用例复用）。"""
    resp = client.post("/api/chat", headers=headers, json={"text": text})
    data = assert_ok(resp)
    sid = data.get("sessionId") or data.get("session_id")
    assert sid, data
    return str(sid)


# ==================================================================== T1 画像聚合
def test_weak_profile_top2_ordering_and_labels():
    """T1：同一学生 METHOD×12 / READ×9 / CALC×3 → weak_tags=[METHOD, READ]，source=profile。"""
    with SessionLocal() as db:
        student_id = _make_student(db)
        qid = _make_question(db)
        for etype, n in (("METHOD", 12), ("READ", 9), ("CALC", 3)):
            for _ in range(n):
                db.add(Mistake(student_id=student_id, question_id=qid, error_type=etype))
        db.commit()

        profile = get_weak_profile(db, student_id=student_id)

    codes = [tag["code"] for tag in profile["weak_tags"]]
    assert codes == ["METHOD", "READ"], f"Top-2 排序错误：{codes}"
    assert profile["source"] == "profile", profile
    # label 一律走 error_label（设计 §7 铁律 1），前端不硬编码中文
    assert profile["weak_tags"][0]["label"] == error_label("METHOD") == "方法错"
    assert profile["weak_tags"][1]["label"] == error_label("READ") == "读题错"
    # 计数第三的 CALC（3 次）应被 Top-2 截断
    assert "CALC" not in codes


def test_weak_profile_excludes_dirty_error_types():
    """T1：error_type='练习错题'（TD-3 脏值）与 NULL 不出现在 weak_tags 里。"""
    with SessionLocal() as db:
        student_id = _make_student(db)
        # 合法 METHOD×5 + 脏值 练习错题×4 + NULL×6
        _seed_mistakes_for(student_id, ("METHOD", 5), ("练习错题", 4), (None, 6))

        # 直接在已提交的库上查询（新会话，验证持久化后聚合正确）
        with SessionLocal() as db2:
            profile = get_weak_profile(db2, student_id=student_id)

    codes = [tag["code"] for tag in profile["weak_tags"]]
    assert codes == ["METHOD"], f"脏值未过滤干净：{codes}"
    assert "练习错题" not in codes
    assert None not in codes
    assert profile["source"] == "profile", profile
    # 再次确认：SQL IN (五类) 天然排除脏值，无任何写操作
    assert all(c in VALID_ERROR_TYPES for c in codes)


def test_weak_profile_fallback_when_no_mistakes():
    """T3：零错题的新学生 → weak_tags=[]，source=fallback。"""
    with SessionLocal() as db:
        student_id = _make_student(db)
        db.commit()
        profile = get_weak_profile(db, student_id=student_id)

    assert profile["weak_tags"] == [], profile
    assert profile["source"] == "fallback", profile


def test_weak_profile_fallback_when_only_dirty_mistakes():
    """T3（加强）：仅脏值错题（NULL + '练习错题'）也算空画像 → fallback。"""
    with SessionLocal() as db:
        student_id = _make_student(db)
        _seed_mistakes_for(student_id, ("练习错题", 3), (None, 2))
        with SessionLocal() as db2:
            profile = get_weak_profile(db2, student_id=student_id)

    assert profile["weak_tags"] == [], profile
    assert profile["source"] == "fallback", profile


def test_weak_profile_top_n_parameter_respected():
    """T1：top_n=1 时只回带最强弱项（验证入参确实生效，不止默认 2）。"""
    with SessionLocal() as db:
        student_id = _make_student(db)
        _seed_mistakes_for(student_id, ("CALC", 20), ("KNOWLEDGE", 11), ("CHECK", 5))
        with SessionLocal() as db2:
            profile = get_weak_profile(db2, student_id=student_id, top_n=1)

    codes = [tag["code"] for tag in profile["weak_tags"]]
    assert codes == ["CALC"], f"top_n=1 应只回带 CALC：{codes}"
    assert len(profile["weak_tags"]) == 1


# ==================================================================== T2 端到端回带
def test_e2e_profile_student_gets_weak_tags(client, monkeypatch, new_phone):
    """T2/T6：有历史错题的学生，POST /exercise/gen 回带非空 weak_tags 且 weak_source=profile。"""
    patch_ark(monkeypatch)
    headers, student_id = _auth(client, new_phone())

    # 造历史错题：METHOD×7 + READ×4（弱项画像应取到 [METHOD, READ]）
    _seed_mistakes_for(student_id, ("METHOD", 7), ("READ", 4))

    session_id = _start_session(client, headers)
    data = assert_ok(
        client.post("/api/exercise/gen", headers=headers, json={"sessionId": session_id})
    )

    # 后端响应键名是 snake_case（设计 §3.3）
    assert "weak_tags" in data, f"响应缺少 weak_tags：{sorted(data)}"
    assert "weak_source" in data, f"响应缺少 weak_source：{sorted(data)}"
    assert data["weak_source"] == "profile", data
    assert isinstance(data["weak_tags"], list) and len(data["weak_tags"]) >= 1, data

    codes = [tag["code"] for tag in data["weak_tags"]]
    assert codes[0] == "METHOD", f"画像最强弱项应为 METHOD：{codes}"
    # 标签非空且为五类合法机器码
    assert all(tag.get("label") for tag in data["weak_tags"])
    assert all(tag["code"] in VALID_ERROR_TYPES for tag in data["weak_tags"])


def test_e2e_fallback_student_no_500_and_empty_tags(client, monkeypatch, new_phone):
    """T2/T6：无历史错题的新学生 → weak_source=fallback、weak_tags=[]、接口不 500。"""
    patch_ark(monkeypatch)
    # 全新手机号 = 全新学生，无任何历史错题
    headers, _ = _auth(client, new_phone())

    session_id = _start_session(client, headers)
    resp = client.post("/api/exercise/gen", headers=headers, json={"sessionId": session_id})
    # 关键：兜底路径绝不能 500（G3 业务目标）
    assert resp.status_code == 200, resp.text

    data = resp.json()["data"]
    assert data["weak_source"] == "fallback", data
    assert data["weak_tags"] == [], data
    # 出题本身仍正常返回两道题，功能不被画像为空阻断
    assert isinstance(data["exercises"], list) and len(data["exercises"]) == 2, data


# ==================================================================== T2 注入校验（可选 → 已实现）
def test_e2e_weak_context_injected_into_prompt(client, monkeypatch, new_phone):
    """T2：source=profile 时，弱项 label 确实被拼进传给出题模型的 context。"""
    ark = patch_ark(monkeypatch)
    headers, student_id = _auth(client, new_phone())
    _seed_mistakes_for(student_id, ("METHOD", 5), ("READ", 3))

    session_id = _start_session(client, headers)
    assert_ok(
        client.post("/api/exercise/gen", headers=headers, json={"sessionId": session_id})
    )

    exercise_calls = [c for c in ark.calls if c["kind"] == "exercise"]
    assert exercise_calls, f"应发生了出题方舟调用，实际调用：{ark.kinds()}"
    prompt = exercise_calls[0]["messages"][0]["content"]

    # context 注入点：定向推题前缀 + 弱项中文标签（error_label）
    assert "【定向推题】" in prompt, prompt
    assert "方法错" in prompt, prompt
    assert "读题错" in prompt, prompt


__all__ = [
    "test_weak_profile_top2_ordering_and_labels",
    "test_weak_profile_excludes_dirty_error_types",
    "test_weak_profile_fallback_when_no_mistakes",
    "test_weak_profile_fallback_when_only_dirty_mistakes",
    "test_weak_profile_top_n_parameter_respected",
    "test_e2e_profile_student_gets_weak_tags",
    "test_e2e_fallback_student_no_500_and_empty_tags",
    "test_e2e_weak_context_injected_into_prompt",
]
